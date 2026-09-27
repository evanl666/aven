"""The approval step.

This is the moment the whole design exists for: the agent has finished, nothing
irreversible has happened yet, and one person looks at one list and decides.
"""

from __future__ import annotations

import asyncio

from aven.terminal.render import DIM, YELLOW, render_outcome, render_tray
from aven.harness.session import Session
from aven.harness.tx import Tray
from aven.text import t


def menu(tray: Tray) -> tuple[str, set[str]]:
    """The keys that would do something, and the line offering them.

    Offering a key that cannot act is worse than not offering it: it reads as a
    promise. A tray holding only finished work has nothing to commit, and
    showing a "commit everything" key next to a calendar event already in
    Calendar invites
    the reader to think it is not there yet.
    """
    keys: list[str] = []
    if tray.pending():
        keys.append(t("review.commit"))
        keys.append(t("review.discard"))
    if tray.undoable():
        keys.append(t("review.undo"))
    keys.append(t("review.keep"))
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
            print(YELLOW(t("review.unknown", choice=choice)))
            continue

        match choice:
            case "c":
                render_outcome(t("review.committed"), await asyncio.to_thread(tray.commit))
            case "d":
                render_outcome(t("review.discarded"), tray.discard())
            case "u":
                rolled = await asyncio.to_thread(tray.undo)
                render_outcome(t("review.undone"), rolled)
                # Rolling the world back without the conversation leaves the
                # model believing the work still stands.
                if rolled and session is not None:
                    point = tray.rewind_point()
                    if point:
                        session.checkout(point)
                        print(DIM(t("review.rewound")))
