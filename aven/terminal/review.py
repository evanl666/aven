"""The approval step.

This is the moment the whole design exists for: the agent has finished, nothing
irreversible has happened yet, and one person looks at one list and decides.
"""

from __future__ import annotations

import asyncio

from aven.terminal.render import DIM, YELLOW, render_outcome, render_tray
from aven.harness.session import Session
from aven.harness.tx import Tray


def menu(tray: Tray) -> tuple[str, set[str]]:
    """The keys that would do something, and the line offering them.

    Offering a key that cannot act is worse than not offering it: it reads as a
    promise. A tray holding only finished work has nothing to commit, and
    showing "[c] 提交全部" next to a calendar event already in Calendar invites
    the reader to think it is not there yet.
    """
    keys: list[str] = []
    if tray.pending():
        keys.append("[c] 提交待确认")
        keys.append("[d] 丢弃待确认")
    if tray.undoable():
        keys.append("[u] 撤销已执行")
    keys.append("[Enter] 保持现状")
    return "   ".join(keys), {k[1] for k in keys if k[1] != "E"}


async def review(tray: Tray, session: Session | None = None) -> None:
    """Show the batch and act on one keystroke. Returns when the user is done."""
    while tray.pending() or tray.undoable():
        render_tray(tray)
        line, allowed = menu(tray)
        print(DIM(line))

        try:
            # Reading a line blocks; off the event loop it goes, like the tools.
            typed = await asyncio.to_thread(input, "> ")
            choice = typed.strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Interrupted means nothing was approved. Staged work stays staged.
            print()
            return

        if choice == "":
            return
        if choice not in allowed:
            # Say so rather than silently leaving: a mistyped key that quietly
            # exits looks exactly like a key that worked.
            print(YELLOW(f"  这里没有 [{choice}] 这个选项"))
            continue

        match choice:
            case "c":
                render_outcome("提交了", await asyncio.to_thread(tray.commit))
            case "d":
                render_outcome("丢弃了", tray.discard())
            case "u":
                rolled = await asyncio.to_thread(tray.undo)
                render_outcome("撤销了", rolled)
                # Rolling the world back without the conversation leaves the
                # model believing the work still stands.
                if rolled and session is not None:
                    point = tray.rewind_point()
                    if point:
                        session.checkout(point)
                        print(DIM("  对话也回退到了改动之前"))
