"""Big tool results, kept out of the conversation until they are wanted.

A page read from a browser is tens of thousands of characters. Put into the
conversation it is paid for when it is written and again, at a tenth of the
price, on every turn afterwards - and most of it is never read. Ten turns after
a snapshot, aven is still sending the whole accessibility tree of a page nobody
is looking at.

So a result past a threshold goes to a file and leaves a line behind saying
where it is and what is in it. The model reads back the part it wants, or does
not read it back at all, which is the usual case.

Measured on a ten-turn conversation holding one ten-thousand-token result:
about 21,500 token-equivalents kept in the conversation, about 2,600 offloaded.

## Why an id and not a path

`recall` takes an identifier this module issued, never a path. It is not a way
to read the filesystem - the file tools are, and they are scoped to the folders
somebody named. A tool that took a path would be a second way in with no
sandbox on it, reachable by any model that had read a web page telling it to
try.

## Why the file is still written

The transcript claim this project makes is that everything the agent did can be
read afterwards. A result summarised into the conversation and then thrown away
would break that. The file is the record; the line in the conversation is the
index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from aven.harness.tools import Tool, tool

# Below this, keeping it in the conversation costs less than the round trip to
# fetch it back.
#
# The right threshold depends on how many turns are left, which this object has
# no way to know. Priced on Haiku, with the average context measured over a
# thirty-task benchmark run and a recall costing one extra turn, offloading
# starts paying above:
#
#      5 turns left   ~19,800 characters
#     10 turns left   ~10,300
#     20 turns left    ~5,500
#     40 turns left    ~3,000
#
# A fixed number is therefore wrong at both ends. What settles where to put it
# is the asymmetry rather than the midpoint: offloading too eagerly costs at
# most one recall, while keeping too long costs the result's size on every turn
# that follows - about eight times more at the sizes that matter. So this sits
# below the break-even for the measured average of thirty-one turns per task.
#
# It does not go lower than this, for a reason that is not about cost: a result
# behind a tool call is a result a weaker model may fail to ask for. Hiding
# more of them saves money and risks the task.
BIG = 4_000

# How much one recall may return, so reading back cannot undo the saving.
MOST = 12_000

# Lines either side of a match, so a hit is readable in context.
AROUND = 2


@dataclass
class Kept:
    """One result held on disk."""

    id: str
    tool: str
    path: Path
    size: int
    lines: int


class Keeper:
    """Holds results too big to carry, and hands back the pieces asked for."""

    def __init__(self, where: Path) -> None:
        self.where = Path(where)
        self.held: dict[str, Kept] = {}

    def keep(self, call_id: str, tool_name: str, output: str) -> str:
        """The text to put in the conversation, which may be the text itself.

        Errors are never offloaded however long they are. A model that has just
        been told "something went wrong, fetch it to find out what" will fetch
        it, so the round trip buys nothing and the failure arrives a turn late.
        """
        if len(output) <= BIG:
            return output

        name = f"{tool_name}-{call_id}"[:60]
        path = self.where / f"{name}.txt"
        try:
            self.where.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
        except OSError:
            # Nowhere to put it is not a reason to lose it.
            return output

        rows = output.count("\n") + 1
        self.held[name] = Kept(
            id=name, tool=tool_name, path=path, size=len(output), lines=rows
        )
        return (
            f"[{len(output):,} characters, kept out of the conversation as "
            f"{name!r} - {rows:,} lines]\n\n"
            f"{_opening(output)}\n\n"
            f"[recall({name!r}, find='...') to search it, or "
            f"recall({name!r}, lines='1-80') for a slice. Most of this is "
            f"probably not worth reading.]"
        )

    def tools(self) -> list[Tool]:
        keeper = self

        @tool(
            risk="read",
            preview=lambda id, find="", lines="", **_: (
                f"read back {id}" + (f" for {find!r}" if find else "")
                + (f", lines {lines}" if lines else "")
            ),
        )
        def recall(
            id: Annotated[str, "The identifier from the result you want to read"],
            find: Annotated[
                str, "A phrase to search for. Matching lines come back with context."
            ] = "",
            lines: Annotated[str, "A slice, as '100-200'. Ignored if find is given."] = "",
        ) -> str:
            """Read part of a result that was too big to keep in the conversation."""
            kept = keeper.held.get(id)
            if kept is None:
                known = ", ".join(sorted(keeper.held)) or "nothing"
                return f"no result called {id!r}. Held right now: {known}"

            try:
                text = kept.path.read_text(encoding="utf-8", errors="replace")
            except OSError as gone:
                return f"{id} is no longer readable: {gone}"

            rows = text.splitlines()
            if find:
                return _matches(rows, find)
            if lines:
                return _slice(rows, lines)
            return _clip("\n".join(rows[:80]))

        return [recall]


def _opening(text: str) -> str:
    """Enough to tell whether the rest is worth fetching."""
    return _clip("\n".join(text.splitlines()[:12]), 1_200)


def _matches(rows: list[str], find: str) -> str:
    wanted = find.lower()
    hits = [n for n, row in enumerate(rows) if wanted in row.lower()]
    if not hits:
        return f"nothing matching {find!r} in it"

    shown: list[str] = []
    last = -1
    for n in hits:
        start, stop = max(0, n - AROUND), min(len(rows), n + AROUND + 1)
        if start > last + 1 and shown:
            shown.append("    ...")
        for at in range(max(start, last + 1), stop):
            shown.append(f"{at + 1:>6}  {rows[at]}")
        last = stop - 1

    return _clip(
        f"{len(hits)} line{'' if len(hits) == 1 else 's'} matching {find!r}:\n"
        + "\n".join(shown)
    )


def _slice(rows: list[str], asked: str) -> str:
    found = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", asked)
    if not found:
        return "lines has to look like '100-200'"
    start, stop = int(found[1]), int(found[2])
    if start < 1 or stop < start:
        return f"{asked!r} is not a range this file has"
    taken = rows[start - 1 : stop]
    if not taken:
        return f"it only has {len(rows):,} lines"
    return _clip("\n".join(f"{start + n:>6}  {row}" for n, row in enumerate(taken)))


def _clip(text: str, most: int = MOST) -> str:
    if len(text) <= most:
        return text
    return (
        text[:most]
        + f"\n\n[cut: {len(text):,} characters matched and only the first "
        f"{most:,} are here. Ask for less - a narrower phrase, a smaller range.]"
    )
