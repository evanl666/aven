"""The approval step.

This is the moment the whole design exists for: the agent has finished, nothing
irreversible has happened yet, and one person looks at one list and decides.
"""

from __future__ import annotations

from aven.cli.render import DIM, render_outcome, render_tray
from aven.core.session import Session
from aven.tx import Tray

MENU = "[c] 提交全部   [d] 丢弃待确认   [u] 撤销已执行   [Enter] 先放着"


def review(tray: Tray, session: Session | None = None) -> None:
    """Show the batch and act on one keystroke. Returns when the user is done."""
    while tray.pending() or tray.undoable():
        render_tray(tray)
        print(DIM(MENU))

        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Interrupted means nothing was approved. Staged work stays staged.
            print()
            return

        match choice:
            case "c":
                render_outcome("提交了", tray.commit())
            case "d":
                render_outcome("丢弃了", tray.discard())
            case "u":
                rolled = tray.undo()
                render_outcome("撤销了", rolled)
                # Rolling the world back without the conversation leaves the
                # model believing the work still stands.
                if rolled and session is not None:
                    point = tray.rewind_point()
                    if point:
                        session.checkout(point)
                        print(DIM("  对话也回退到了改动之前"))
            case _:
                return
