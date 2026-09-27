"""The sessions on disk, listed so one can be chosen.

`-c` reaches the most recent one and `--session <path>` reaches an exact one.
Between those two there was nothing, and `--fork` made that a real problem: it
writes new files by design, so after a couple of detours the conversation you
want is neither the latest nor a path you can remember.

Read with json.loads rather than `Session.open`, deliberately. A card needs a
name, an opening line and a count; building every message into a dataclass to
throw them all away costs the whole directory's worth of parsing on a keystroke.
The two readers agree on one thing only - the file is one JSON object per line -
and that is the contract the format was chosen for.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

# Enough of the opening line to recognise which conversation this was.
OPENING = 46


@dataclass(frozen=True)
class Card:
    """One session, as much of it as a picker needs."""

    path: Path
    name: str | None
    opening: str
    messages: int
    when: float

    @property
    def title(self) -> str:
        """What to call it: its name if it has one, else how it started."""
        return self.name or self.opening or "(空会话)"


def card(path: Path) -> Card:
    """Read one session file down to a single line of description.

    A file being written to right now, or half-written by a crash, must not stop
    the whole listing - the point of a picker is to reach the others.
    """
    name: str | None = None
    opening = ""
    count = 0

    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    # A torn last line. Everything before it still counts.
                    break
                count += 1
                kind = entry.get("kind")
                if kind == "meta" and entry.get("name"):
                    # Last one wins, as Session.name has it.
                    name = entry["name"]
                elif kind == "user" and not opening:
                    # The first user message, whatever opened it. Not filtered
                    # by source: the first one is always what started the
                    # conversation, and when that is a cron tick rather than a
                    # person, the tick is exactly what you want to see here.
                    opening = " ".join(entry.get("text", "").split())[:OPENING]
    except OSError:
        pass

    return Card(
        path=path,
        name=name,
        opening=opening,
        messages=count,
        when=path.stat().st_mtime if path.exists() else 0.0,
    )


def catalogue(directory: Path, *, limit: int = 20) -> list[Card]:
    """The sessions in a directory, most recently touched first."""
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        return []

    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [card(path) for path in files[:limit]]


def render(cards: list[Card]) -> str:
    """The numbered list to show. Numbers because they are what gets typed."""
    if not cards:
        return "没有会话记录"

    lines = []
    for n, entry in enumerate(cards, 1):
        lines.append(f"{n:>3}. {entry.title}")
        lines.append(f"     {ago(entry.when)} · {entry.messages} 条 · {entry.path.name}")
    return "\n".join(lines)


def ago(when: float, *, now: float | None = None) -> str:
    """How long ago, roughly.

    Roughly on purpose. "3 天前" is what tells you whether this is the thing you
    were doing before lunch; a timestamp makes you do the subtraction yourself.
    """
    seconds = max(0.0, (time.time() if now is None else now) - when)
    for limit, size, unit in (
        (60, 1, "秒"),
        (3600, 60, "分钟"),
        (86400, 3600, "小时"),
        (86400 * 30, 86400, "天"),
    ):
        if seconds < limit:
            return f"{int(seconds // size)} {unit}前"
    return time.strftime("%Y-%m-%d", time.localtime(when))
