"""Turning events into terminal output.

The loop yields; this file is the only thing that decides what a person sees.
Nothing here can change what the agent does - a renderer that could would be a
renderer that can break the agent.
"""

from __future__ import annotations

import os
import sys

from aven.core.events import AgentEnd, Event, MessageEnd, ToolEnd, ToolStart
from aven.tx import Entry, Tray

_COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


DIM = lambda s: paint(s, "2")
BOLD = lambda s: paint(s, "1")
RED = lambda s: paint(s, "31")
GREEN = lambda s: paint(s, "32")
YELLOW = lambda s: paint(s, "33")


def render(event: Event, *, verbose: bool = False) -> None:
    """Print one event. Silent for the events a person does not need."""
    match event:
        case MessageEnd() if event.message.kind == "assistant" and event.message.text:
            print(f"\n{event.message.text}")

        case ToolStart():
            args = ", ".join(f"{k}={v!r}" for k, v in event.call.args.items())
            print(DIM(f"  · {event.call.name}({_clip(args, 70)})"))

        case ToolEnd():
            if event.staged:
                print(YELLOW(f"    ⏸ 已暂存,等你确认"))
            elif event.result.is_error:
                print(RED(f"    ✗ {_clip(event.result.output, 90)}"))
            elif verbose:
                print(DIM(f"    ✓ {_clip(event.result.output, 90)}"))

        case AgentEnd() if event.reason == "max_turns":
            print(RED("\n■ 达到轮次上限,任务没有做完"))


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


def _clip(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
