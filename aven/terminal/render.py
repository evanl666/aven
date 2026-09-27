"""Turning events into terminal output.

The loop yields; this file is the only thing that decides what a person sees.
Nothing here can change what the agent does - a renderer that could would be a
renderer that can break the agent.

Rendering a stream needs memory, so this is a class: whether text has started
arriving decides where the newlines go, and a spinner has to be stopped before
anything else is written to the same line.
"""

from __future__ import annotations

import difflib
import itertools
import os
import sys
import threading

from aven.harness.events import (
    AgentEnd,
    Event,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
)
from aven.harness.tx import Entry, Tray
from aven.text import t
from aven.harness.tools import Body, Detail, Diff, Moves, Order

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

            case MessageEnd() if event.message.kind == "summary":
                self._quiet()
                print(DIM("\n  ⧗ " + t("render.compacted")))

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
                if self.verbose and event.detail is not None:
                    for line in draw(event.detail):
                        print(DIM(f"      {line}"))

            case AgentEnd():
                self._quiet()
                if event.reason == "max_turns":
                    print(RED("\n■ " + t("render.max_turns")))
                elif event.reason == "truncated":
                    print(RED("\n■ " + t("render.truncated")))

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
            return YELLOW("⏸ " + t("tray.staged"))
        return RED("✗") if event.result.is_error else GREEN("✓")


def render_tray(tray: Tray) -> None:
    """The batch, as the user decides on it."""
    pending, undoable = tray.pending(), tray.undoable()
    if not pending and not undoable:
        return

    print()
    if pending:
        print(BOLD(t("render.pending_header", n=len(pending))))
        for entry in pending:
            print(YELLOW(f"  ⏸  {entry.preview}"))
            # Pending work is what a person is about to approve, so it gets the
            # detail whether or not they asked for verbose. A line is enough to
            # recognise a call and not enough to decide on one.
            for line in draw(entry.detail):
                print(DIM(f"      {line}"))
    if undoable:
        print(DIM(t("render.undoable_header", n=len(undoable))))
        for entry in undoable:
            print(DIM(f"  ✓  {entry.preview}"))


# How much of a detail to show in a terminal. A window scrolls; a transcript
# does not, and forty lines of diff between two tool calls loses the shape.
DETAIL_LINES = 12


def draw(detail: Detail | None) -> list[str]:
    """A detail as terminal lines, or nothing.

    The terminal is the poorest surface a detail will be drawn on, which is what
    makes this worth having rather than leaving the shapes for a window that does
    not exist yet: if it reads here, it will read anywhere.
    """
    match detail:
        case None:
            return []
        case Diff():
            return _clip_lines(_diff_lines(detail))
        case Body():
            head = [detail.title] if detail.title else []
            return _clip_lines(head + detail.text.splitlines())
        case Moves():
            return _clip_lines([f"{src}  →  {dst}" for src, dst in detail.pairs])
        case Order():
            # A separator rather than padding: _clip_lines collapses runs of
            # whitespace, so a column lined up here would not survive the trip.
            lines = [f"{what}  —  {price}" for what, price in detail.items]
            lines.append(t("order.total", total=detail.total))
            for key, value in (
                ("order.where", detail.where),
                ("order.account", detail.account),
                ("order.arrives", detail.arrives),
            ):
                if value:
                    lines.append(t(key, value=value))
            return _clip_lines(lines)
    return []


def _diff_lines(detail: Diff) -> list[str]:
    """The change as -/+ lines.

    difflib rather than printing both sides: what a person needs to see is the
    few lines that differ, and for a whole-file write the two sides are almost
    entirely the same text.
    """
    lines = list(
        difflib.unified_diff(
            detail.before.splitlines(),
            detail.after.splitlines(),
            fromfile=detail.path or "before",
            tofile=detail.path or "after",
            lineterm="",
            n=1,
        )
    )
    # The ---/+++ header repeats the path the preview already said.
    return [line for line in lines if not line.startswith(("---", "+++"))]


def _clip_lines(lines: list[str]) -> list[str]:
    kept = [_clip(line, 100) for line in lines[:DETAIL_LINES]]
    if len(lines) > DETAIL_LINES:
        kept.append(f"... {len(lines) - DETAIL_LINES} more lines")
    return kept


def render_outcome(verb: str, entries: list[Entry]) -> None:
    print(
        GREEN(t("render.outcome", verb=verb, n=len(entries)))
        if entries
        else DIM(t("render.no_change"))
    )
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
        long = isinstance(value, str) and len(value) > 40
        shown = t("render.clipped", n=len(value)) if long else repr(value)
        parts.append(f"{key}={shown}")
    return _clip(", ".join(parts), 70)


def _clip(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
