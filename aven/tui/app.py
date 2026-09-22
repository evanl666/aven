"""aven as a full-screen terminal app.

A client of the loop, exactly like the plain CLI: it drives run(), draws the
events, and asks the tray to commit or roll back. What it adds is what a
line-by-line terminal cannot do - a tray that updates while the agent works,
and an interruption that stops the task without leaving the program.

A turn runs as a Textual worker. That is what makes Esc possible: cancelling
the worker cancels the task, the cancellation lands inside run() at whatever
it was awaiting, and run() closes any tool call it leaves unanswered before
letting it through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input
from textual.worker import Worker, WorkerState

from aven.core.agent import ModelFn, run
from aven.core.events import (
    AgentEnd,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
    TurnStart,
)
from aven.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from aven.core.session import Session
from aven.core.tools import Tool
from aven.tui.widgets import Note, Reply, Thinking, ToolLine, TrayPanel, UserLine
from aven.tx import Tray

HELP = """\
/undo      撤销已执行的改动(对话也一起回退)
/commit    执行等待确认的操作
/discard   丢弃等待确认的操作
/cost      这次会话用了多少 token
/clear     清屏(不影响会话记录)
/quit      退出

Esc 中断当前任务 · Ctrl+T 显示/隐藏暂存区 · Ctrl+Q 退出"""


class AvenApp(App[None]):
    TITLE = "aven"

    CSS = """
    #main { height: 1fr; }
    #transcript { width: 1fr; padding: 0 2; }
    #tray { width: 42; padding: 0 1; border-left: tall $panel; }
    #tray.hidden { display: none; }
    #tray .title { margin: 1 0; }
    #tray-actions { height: auto; margin-top: 1; }
    #tray-actions Button { min-width: 10; margin-right: 1; }
    #prompt { margin: 0 1; }
    UserLine { margin-top: 1; color: $accent; text-style: bold; }
    Reply { margin: 0; }
    ToolLine { color: $text-muted; }
    Thinking { color: $text-muted; margin-top: 1; }
    Note { color: $warning; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "中断"),
        Binding("ctrl+t", "toggle_tray", "暂存区"),
        Binding("ctrl+l", "clear", "清屏"),
        Binding("ctrl+q", "quit", "退出"),
    ]

    def __init__(
        self,
        *,
        session: Session,
        model: ModelFn,
        tools: Sequence[Tool],
        root: Path,
        max_turns: int = 12,
    ) -> None:
        super().__init__()
        self.session = session
        self.model = model
        self.tools = list(tools)
        self.root = root
        self.max_turns = max_turns
        self.tray = Tray()
        self._turn: Worker[None] | None = None

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield VerticalScroll(id="transcript")
            yield TrayPanel(id="tray")
        yield Input(placeholder="说点什么 · /help 看命令", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        self._replay_history()
        self._refresh_tray()
        self.query_one("#prompt", Input).focus()

    # -- input ---------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            await self._command(text)
            return
        if self.busy:
            self.notify("上一个任务还在跑,按 Esc 中断它", severity="warning")
            return

        await self._say(UserLine(text))
        self._turn = self.run_worker(
            self._run_turn(text), group="turn", exclusive=True, exit_on_error=False
        )
        self._refresh_tray()

    async def _command(self, text: str) -> None:
        name = text.split()[0].lower()
        match name:
            case "/undo":
                await self._undo()
            case "/commit":
                await self._commit()
            case "/discard":
                self._discard()
            case "/cost":
                await self._say(Note(str(getattr(self.model, "usage", "没有用量信息"))))
            case "/clear":
                self.action_clear()
            case "/help":
                await self._say(Note(HELP))
            case "/quit" | "/exit":
                self.exit()
            case _:
                await self._say(Note(f"没有 {name} 这个命令,/help 看看有哪些"))

    # -- a turn --------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._turn is not None and self._turn.state in (
            WorkerState.PENDING,
            WorkerState.RUNNING,
        )

    async def _run_turn(self, prompt: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        thinking = Thinking()
        await transcript.mount(thinking)
        transcript.scroll_end(animate=False)

        stream = None
        lines: dict[str, ToolLine] = {}

        async def place(widget: Any) -> None:
            # Everything goes above the spinner, so it always sits at the end.
            await transcript.mount(widget, before=thinking)
            transcript.scroll_end(animate=False)

        try:
            async for event in run(
                session=self.session,
                prompt=prompt,
                model=self.model,
                tools=self.tools,
                tray=self.tray,
                max_turns=self.max_turns,
            ):
                match event:
                    case TurnStart():
                        thinking.display = True

                    case MessageDelta():
                        if stream is None:
                            thinking.display = False
                            reply = Reply()
                            await place(reply)
                            stream = Reply.get_stream(reply)
                        await stream.write(event.text)
                        transcript.scroll_end(animate=False)

                    case MessageEnd() if isinstance(event.message, AssistantMessage):
                        if stream is not None:
                            await stream.stop()
                            stream = None
                        elif event.message.text:
                            await place(Reply(event.message.text))

                    case ToolStart():
                        thinking.display = False
                        lines[event.call.id] = ToolLine(event.call)
                        await place(lines[event.call.id])

                    case ToolEnd():
                        lines[event.call.id].finish(event)
                        self._refresh_tray()
                        thinking.display = True

                    case AgentEnd() if event.reason == "max_turns":
                        await place(Note("达到轮次上限,任务没有做完"))
        finally:
            # Runs on success, on error and on Esc alike. Awaiting here is
            # fine even while cancelling: the cancellation has already been
            # delivered, and cleanup finishes before it travels on.
            if stream is not None:
                await stream.stop()
            await thinking.remove()
            self._update_subtitle()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._turn:
            return
        if event.state == WorkerState.CANCELLED:
            self.run_worker(self._say(Note("已中断。已做的可撤销改动还在暂存区里。")))
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            self.run_worker(self._say(Note(f"出错了:{type(error).__name__}: {error}")))
        if event.state in (WorkerState.SUCCESS, WorkerState.CANCELLED, WorkerState.ERROR):
            self._refresh_tray()

    # -- the tray ------------------------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "commit":
                await self._commit()
            case "discard":
                self._discard()
            case "undo":
                await self._undo()

    async def _commit(self) -> None:
        if self.busy or not self.tray.pending():
            return
        done = await asyncio.to_thread(self.tray.commit)
        failed = [e for e in self.tray.entries if e.state == "failed"]
        message = f"提交了 {len(done)} 项"
        if failed:
            message += f",{failed[0].preview} 失败:{failed[0].output}"
        await self._say(Note(message))
        self._settle()

    def _discard(self) -> None:
        if self.busy or not self.tray.pending():
            return
        dropped = self.tray.discard()
        self.run_worker(self._say(Note(f"丢弃了 {len(dropped)} 项,它们没有发生过")))
        self._settle()

    async def _undo(self) -> None:
        if self.busy or not self.tray.undoable():
            return
        point = self.tray.rewind_point()
        rolled = await asyncio.to_thread(self.tray.undo)
        # The world went back; the conversation has to go back with it, or
        # the model carries on believing the work still stands.
        if rolled and point:
            self.session.checkout(point)
        await self._say(Note(f"撤销了 {len(rolled)} 项,对话也回到了改动之前"))
        self._settle()

    def _settle(self) -> None:
        """Start a fresh tray once nothing in this one is waiting on a decision."""
        if not self.tray.pending() and not self.tray.undoable():
            self.tray = Tray()
        self._refresh_tray()

    def _refresh_tray(self) -> None:
        self.query_one(TrayPanel).show(self.tray, busy=self.busy)

    # -- actions -------------------------------------------------------------

    def action_interrupt(self) -> None:
        if self.busy and self._turn is not None:
            self._turn.cancel()

    def action_toggle_tray(self) -> None:
        self.query_one("#tray").toggle_class("hidden")

    def action_clear(self) -> None:
        self.query_one("#transcript").remove_children()

    # -- helpers -------------------------------------------------------------

    async def _say(self, widget: Any) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(widget)
        transcript.scroll_end(animate=False)

    def _update_subtitle(self) -> None:
        name = getattr(self.model, "model", "model")
        parts = [str(self.root), name, f"{len(self.tools)} 个工具"]
        usage = getattr(self.model, "usage", None)
        if usage is not None and getattr(usage, "requests", 0):
            parts.append(str(usage))
        self.sub_title = " · ".join(parts)

    def _replay_history(self) -> None:
        """Show an earlier session when continuing one with -c."""
        transcript = self.query_one("#transcript", VerticalScroll)
        for message in self.session.history():
            if isinstance(message, UserMessage):
                transcript.mount(UserLine(message.text))
            elif isinstance(message, AssistantMessage) and message.text:
                transcript.mount(Reply(message.text))
            elif isinstance(message, ToolResultMessage):
                mark = "✗" if message.is_error else "✓"
                transcript.mount(Note(f"  · {message.tool_name}  {mark}"))
        transcript.scroll_end(animate=False)
