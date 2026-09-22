"""Turning events into terminal output.

The loop yields; this file is the only thing that decides what a person sees.
Nothing here can change what the agent does - a renderer that could would be a
renderer that can break the agent.

Rendering a stream needs memory, so this is a class: whether text has started
arriving decides where the newlines go, and a spinner has to be stopped before
anything else is written to the same line.
"""

from __future__ import annotations

import itertools
import os
import sys
import threading

from aven.core.events import (
    AgentEnd,
    Event,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
)
from aven.tx import Entry, Tray

# One check for both colour and animation: piping to a file should produce
# neither escape codes nor a spinner that redraws a line no one is watching.
LIVE = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if LIVE else text


DIM = lambda s: paint(s, "2")
BOLD = lambda s: paint(s, "1")
RED = lambda s: paint(s, "31")
GREEN = lambda s: paint(s, "32")
YELLOW = lambda s: paint(s, "33")

CLEAR_LINE = "\r\033[K"


class Spinner:
    """A frame on one line, redrawn from a thread until told to stop.

    A thread because the loop is blocked in `next()` on the network while this
    runs - there is no other moment to draw in. It is a daemon thread and it
    only ever writes to the line it owns.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    PERIOD = 0.08

    def __init__(self, prefix: str = "", label: str = "") -> None:
        self.prefix = prefix
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Spinner:
        if LIVE:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        for step in itertools.count():
            if self._stop.wait(self.PERIOD):
                return
            frame = self.FRAMES[step % len(self.FRAMES)]
            sys.stdout.write(f"{CLEAR_LINE}{self.prefix}{DIM(frame)} {DIM(self.label)}")
            sys.stdout.flush()

    def stop(self) -> None:
        """Stop and blank the line, so the caller can write the final version."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
            sys.stdout.write(CLEAR_LINE)
            sys.stdout.flush()


class Renderer:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._spinner: Spinner | None = None
        self._streaming = False
        self._tool_line = ""

    def handle(self, event: Event) -> None:
        match event:
            case MessageDelta():
                # First chunk ends the wait: drop the spinner, open a line.
                if not self._streaming:
                    self._quiet()
                    self._streaming = True
                    print()
                sys.stdout.write(event.text)
                sys.stdout.flush()

            case MessageEnd() if event.message.kind == "assistant":
                self._quiet()
                if self._streaming:
                    print()
                    self._streaming = False
                elif event.message.text:
                    # A model that does not stream still has to be shown.
                    print(f"\n{event.message.text}")

            case ToolStart():
                self._quiet()
                self._tool_line = f"  {DIM('·')} {event.call.name}({format_arguments(event.call.args)})"
                self._spinner = Spinner(prefix=self._tool_line + " ").start()

            case ToolEnd():
                self._quiet()
                print(f"{self._tool_line}  {self._mark(event)}")
                if event.result.is_error:
                    print(RED(f"      {_clip(event.result.output, 90)}"))
                elif self.verbose and not event.staged:
                    print(DIM(f"      {_clip(event.result.output, 90)}"))

            case AgentEnd():
                self._quiet()
                if event.reason == "max_turns":
                    print(RED("\n■ 达到轮次上限,任务没有做完"))

    def waiting(self, label: str) -> None:
        """Show that something is happening before the first event arrives."""
        self._quiet()
        self._spinner = Spinner(label=label).start()

    def _quiet(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    @staticmethod
    def _mark(event: ToolEnd) -> str:
        if event.staged:
            return YELLOW("⏸ 已暂存")
        return RED("✗") if event.result.is_error else GREEN("✓")


def render_tray(tray: Tray) -> None:
    """The batch, as the user decides on it."""
    pending, undoable = tray.pending(), tray.undoable()
    if not pending and not undoable:
        return

    print()
    if pending:
        print(BOLD(f"{len(pending)} 项等待确认"))
        for entry in pending:
            print(YELLOW(f"  ⏸  {entry.preview}"))
    if undoable:
        print(DIM(f"{len(undoable)} 项已执行(可撤销)"))
        for entry in undoable:
            print(DIM(f"  ✓  {entry.preview}"))


def render_outcome(verb: str, entries: list[Entry]) -> None:
    print(GREEN(f"  {verb} {len(entries)} 项") if entries else DIM("  没有变化"))
    for entry in entries:
        if entry.state == "failed":
            print(RED(f"  ! {entry.preview} — {entry.output}"))


def format_arguments(args: dict[str, object]) -> str:
    """Render a call's arguments, folding away anything long.

    A file's whole contents arriving as one argument would otherwise take the
    terminal with it, and the length is the only part worth seeing anyway.
    """
    parts = []
    for key, value in args.items():
        shown = f"<{len(value)} 字符>" if isinstance(value, str) and len(value) > 40 else repr(value)
        parts.append(f"{key}={shown}")
    return _clip(", ".join(parts), 70)


def _clip(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
