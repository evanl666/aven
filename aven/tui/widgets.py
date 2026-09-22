"""The pieces of the aven screen.

Every string that came from the user, the model or the file system is wrapped
in a rich Text before it reaches a widget. Static.update() parses markup by
default, so a file called "[red]draft[/red].md" or a model reply mentioning
"[b]" would otherwise be read as formatting instructions - harmless at best,
and at worst a line that renders as something other than what happened.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Markdown, Static

from aven.cli.render import format_arguments
from aven.core.events import ToolEnd
from aven.core.messages import ToolCall
from aven.tx import Tray

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class UserLine(Static):
    """What the person asked."""

    def __init__(self, text: str) -> None:
        super().__init__(Text("› ") + Text(text))


class Reply(Markdown):
    """What the model said. Streamed into while it arrives."""


class Note(Static):
    """Something aven itself wants to say - an undo, an interruption."""

    def __init__(self, text: str) -> None:
        super().__init__(Text(text))


class Thinking(Static):
    """A spinner for the silence before the first word or between tools.

    Driven by a timer on the event loop, not a thread: in a Textual app the
    loop is never blocked, so there is always a moment to redraw.
    """

    def __init__(self, label: str = "思考中") -> None:
        super().__init__()
        self.label = label
        self._step = 0

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        self._step += 1
        self.update(Text(f"{SPINNER[self._step % len(SPINNER)]} {self.label}"))


class ToolLine(Static):
    """One tool call: shown while it runs, then marked with how it ended."""

    def __init__(self, call: ToolCall) -> None:
        self.call_text = f"· {call.name}({format_arguments(call.args)})"
        super().__init__(Text(self.call_text + "  …"))

    def finish(self, event: ToolEnd) -> None:
        if event.staged:
            mark, style = "⏸ 已暂存,等你确认", "yellow"
        elif event.result.is_error:
            mark, style = "✗", "red"
        else:
            mark, style = "✓", "green"

        line = Text(self.call_text + "  ") + Text(mark, style=style)
        if event.result.is_error:
            line += Text("\n    " + " ".join(event.result.output.split())[:120], style="red")
        self.update(line)


class TrayPanel(Vertical):
    """The staging tray, always visible, updated as each tool finishes.

    Buttons follow the same rule as the CLI menu: only the ones that would do
    something are shown, and none are pressable while a turn is still running.
    """

    def compose(self) -> ComposeResult:
        yield Static(Text("暂存区", style="bold"), classes="title")
        yield Static(id="tray-body")
        with Horizontal(id="tray-actions"):
            yield Button("提交待确认", id="commit", variant="success")
            yield Button("丢弃", id="discard", variant="warning")
            yield Button("撤销已执行", id="undo", variant="error")

    def show(self, tray: Tray, *, busy: bool) -> None:
        pending, undoable = tray.pending(), tray.undoable()

        body = Text()
        if not pending and not undoable:
            body.append("还没有改动", style="dim")
        if pending:
            body.append(f"{len(pending)} 项等待确认\n", style="bold")
            for entry in pending:
                body.append(f"⏸ {entry.preview}\n", style="yellow")
        if undoable:
            if pending:
                body.append("\n")
            body.append(f"{len(undoable)} 项已执行(可撤销)\n", style="dim")
            for entry in undoable:
                body.append(f"✓ {entry.preview}\n", style="dim")
        self.query_one("#tray-body", Static).update(body)

        self.query_one("#commit", Button).display = bool(pending)
        self.query_one("#discard", Button).display = bool(pending)
        self.query_one("#undo", Button).display = bool(undoable)
        for button in self.query(Button):
            button.disabled = busy
