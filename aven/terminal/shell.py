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
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input
from textual.worker import Worker, WorkerState

from aven.harness.agent import ModelFn, run
from aven.harness.compact import Compactor
from aven.harness.events import (
    AgentEnd,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
    TurnStart,
)
from aven.harness.messages import (
    AssistantMessage,
    new_id,
    SummaryMessage,
    ToolResultMessage,
    UserMessage,
)
from aven.harness.session import Session
from aven.harness.triggers import Trigger, due, keep, remember
from aven.harness.steering import Steering
from aven.harness.toolbox import ToolSource, resolve
from aven.harness.tree import render as render_tree
from aven.terminal.widgets import Note, Reply, Thinking, ToolLine, TrayPanel, UserLine
from aven.harness.tx import Policy, Standing, Tray
from aven.text import t

class Shell(App[None]):
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
        Binding("escape", "interrupt", t("shell.bind.interrupt")),
        Binding("ctrl+t", "toggle_tray", t("shell.bind.tray")),
        Binding("ctrl+l", "clear", t("shell.bind.clear")),
        Binding("ctrl+q", "quit", t("shell.bind.quit")),
    ]

    def __init__(
        self,
        *,
        session: Session,
        model: ModelFn,
        tools: ToolSource,
        root: Path,
        compactor: Compactor | None = None,
        policy: Policy | None = None,
        standing: Standing | None = None,
        triggers: list[Trigger] | None = None,
        max_turns: int = 12,
    ) -> None:
        super().__init__()
        self.session = session
        self.model = model
        # Kept as the source, not resolved: it may be a ToolBox that grows when
        # the model brings a group in, and a list copy would freeze it at
        # whatever was loaded when the app started.
        self.tools = tools
        self.root = root
        self.compactor = compactor
        self.policy = policy
        self.standing = standing

        # Triggers belong here rather than only in --watch: this process stays
        # up, so the tray keeps what fired while you were away and you approve
        # when you sit down. A headless run cannot do that - a staged call's
        # apply is a closure and does not outlive its process.
        self.triggers = list(triggers or [])
        self.remembered = remember()
        self.max_turns = max_turns
        self.tray = Tray(policy=policy, standing=standing)
        self.steering = Steering()
        self._turn: Worker[None] | None = None

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield VerticalScroll(id="transcript")
            yield TrayPanel(id="tray")
        yield Input(placeholder=t("shell.placeholder"), id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        if self.triggers:
            # A minute is plenty: the finest thing a trigger asks for is a time
            # of day, and a folder that changed is still changed a minute later.
            self.set_interval(60, self._check_triggers)
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
            # Not an error and not a reason to make them wait. It goes in after
            # the turn in flight finishes its tool calls, which is the next
            # moment a user message can be added without malforming the
            # conversation.
            self.steering.add(text)
            await self._say(UserLine(text))
            await self._say(Note("↳ " + t("shell.queued", n=self.steering.waiting())))
            return

        await self._say(UserLine(text))
        self._turn = self.run_worker(
            self._run_turn(text), group="turn", exclusive=True, exit_on_error=False
        )
        self._refresh_tray()

    def _check_triggers(self) -> None:
        """Fire whatever is due, unless something is already running.

        Skipped rather than queued while busy. A trigger is "look at this now",
        and one that waited out a long task is asking about a moment that has
        passed - it will come round again.
        """
        if self.busy:
            return
        ready = due(self.triggers, self.remembered)
        keep(self.remembered)
        if not ready:
            return

        trigger = ready[0]
        self.run_worker(self._say(Note(t("shell.fired", name=trigger.name))))
        self._turn = self.run_worker(
            self._run_turn(trigger.prompt, source=trigger.source),
            group="turn", exclusive=True, exit_on_error=False,
        )

    async def _command(self, text: str) -> None:
        name = text.split()[0].lower()
        match name:
            case "/session":
                await self._say(Note(self._describe()))
            case "/name":
                await self._rename(text)
            case "/tree":
                await self._tree(text)
            case "/fork":
                await self._fork(text)
            case "/undo":
                await self._undo()
            case "/commit":
                await self._commit()
            case "/discard":
                self._discard()
            case "/cost":
                await self._say(Note(str(getattr(self.model, "usage", None) or t("shell.no_usage"))))
            case "/clear":
                self.action_clear()
            case "/help":
                await self._say(Note(t("shell.help")))
            case "/quit" | "/exit":
                self.exit()
            case _:
                await self._say(Note(t("shell.unknown_command", name=name)))

    # -- a turn --------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._turn is not None and self._turn.state in (
            WorkerState.PENDING,
            WorkerState.RUNNING,
        )

    async def _run_turn(self, prompt: str, source: str = "chat") -> None:
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
                compactor=self.compactor,
                steering=self.steering,
                max_turns=self.max_turns,
                source=source,
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

                    case MessageEnd() if isinstance(event.message, SummaryMessage):
                        await place(Note("⧗ " + t("render.compacted")))

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
                        await place(Note(t("render.max_turns")))

                    case AgentEnd() if event.reason == "truncated":
                        await place(Note(t("render.truncated")))
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
            # Whatever was queued was never read, so it goes back to the editor
            # rather than being thrown away with the run that would have read
            # it. One line only: more than that, and the rest would be lost.
            returned = self.steering.drain()
            if returned:
                self.query_one("#prompt", Input).value = returned[0]
            self.run_worker(self._say(Note(t("shell.interrupted"))))
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            self.run_worker(self._say(Note(t("shell.error", kind=type(error).__name__, problem=error))))
        if event.state in (WorkerState.SUCCESS, WorkerState.CANCELLED, WorkerState.ERROR):
            self._refresh_tray()

    # -- the tree ------------------------------------------------------------

    async def _tree(self, text: str) -> None:
        """Show the branches, or move to one.

        The tree has been in the file since the first session was written. This
        is the first thing that lets anyone see it, and `/tree <id>` is the
        first thing that lets them move without going through undo.
        """
        parts = text.split()
        if len(parts) == 1:
            await self._say(Note(render_tree(self.session)))
            return

        try:
            target = self.session.find(parts[1])
        except KeyError as problem:
            await self._say(Note(str(problem)))
            return

        if self.busy:
            await self._say(Note(t("shell.busy")))
            return

        leaving = self.session.head
        self.session.checkout(target)
        # The tray belongs to the branch that was left. Its undos point at work
        # done on a path we are no longer on, and firing one from here would
        # roll back something this branch never did.
        self.tray = Tray(policy=self.policy, standing=self.standing)
        self.action_clear()
        self._replay_history()
        await self._say(Note(t("shell.moved", id=target[:6])))
        self._refresh_tray()

        # What the branch we just left found out would otherwise be thrown away:
        # it is on a path this one does not include, so the new branch would
        # repeat every file read and every dead end.
        if self.compactor is not None and leaving and leaving != target:
            await self._carry(leaving)

    async def _carry(self, leaving: str) -> None:
        """Summarise the branch being left onto the one being entered."""
        waiting = Note("⧗ " + t("shell.carrying"))
        await self._say(waiting)
        try:
            carried = await self.compactor.summarise_branch(self.session, leaving)
        except Exception as problem:
            # A failed summary is not a failed checkout. The branch move already
            # happened and is still correct; this only means the new branch
            # starts without what the old one learned.
            await waiting.remove()
            await self._say(Note(t("shell.carry_failed", kind=type(problem).__name__)))
            return

        await waiting.remove()
        if carried is not None:
            await self._say(Note("↳ " + t("shell.carried", gist=carried.text.splitlines()[0][:60] + "…")))

    async def _rename(self, text: str) -> None:
        # Not `_name`: Textual's DOMNode already owns that attribute, and an
        # instance shadowing it with a method is a TypeError at the call site.
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            await self._say(Note(self.session.name or t("shell.unnamed")))
            return
        self.session.rename(parts[1])
        self._update_subtitle()
        await self._say(Note(t("shell.named", name=self.session.name)))

    def _describe(self) -> str:
        """What /session shows: enough to know which file you are in."""
        session = self.session
        lines = [
            t("shell.describe.name", name=session.name or t("shell.describe.none")),
            t("shell.describe.file", path=session.path),
            t("shell.describe.messages", n=len(session), branches=len(session.leaves())),
            t("shell.describe.head", id=(session.head or "")[:6]),
        ]
        usage = getattr(self.model, "usage", None)
        if usage is not None:
            lines.append(t("shell.describe.usage", usage=usage))
        return "\n".join(lines)

    async def _fork(self, text: str) -> None:
        """Copy this branch into its own session file and continue there."""
        if self.busy:
            await self._say(Note(t("shell.busy")))
            return

        parts = text.split()
        at = parts[1] if len(parts) > 1 else None
        # The random tail is what keeps two forks in the same second apart;
        # fork() refuses an existing file rather than merging into it.
        destination = self.session.path.with_name(
            f"{time.strftime('%Y%m%d-%H%M%S')}-{new_id()[:4]}-fork.jsonl"
        )
        try:
            forked = self.session.fork(destination, at=at)
        except (KeyError, FileExistsError) as problem:
            await self._say(Note(str(problem)))
            return

        self.session = forked
        self.tray = Tray(policy=self.policy, standing=self.standing)
        self.action_clear()
        self._replay_history()
        await self._say(
            Note(t("shell.forked", file=forked.path.name, n=len(forked)))
        )
        self._update_subtitle()
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
        message = t("shell.committed", n=len(done))
        if failed:
            message += t("shell.commit_failed", preview=failed[0].preview, output=failed[0].output)
        await self._say(Note(message))
        self._settle()

    def _discard(self) -> None:
        if self.busy or not self.tray.pending():
            return
        dropped = self.tray.discard()
        self.run_worker(self._say(Note(t("shell.discarded", n=len(dropped)))))
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
        await self._say(Note(t("shell.undone", n=len(rolled))))
        self._settle()

    def _settle(self) -> None:
        """Start a fresh tray once nothing in this one is waiting on a decision."""
        if not self.tray.pending() and not self.tray.undoable():
            self.tray = Tray(policy=self.policy, standing=self.standing)
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
        parts = [self.session.name or str(self.root), name,
                 t("shell.tools", n=len(resolve(self.tools)))]
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
