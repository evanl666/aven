"""The staging tray: what the agent did, and what it wants permission to do.

Every harness that acts on a person's machine has to answer one question: when
does a side effect actually happen? The usual answer is a permission popup per
action, which trains people to click allow. aven answers it with risk:

    read          runs, and is not worth recording
    reversible    runs, and its undo is kept
    irreversible  does NOT run - it is staged, and the model is told so

The agent therefore finishes its whole task without ever sending, paying, or
deleting. What the user reviews at the end is one batch with a preview per line,
not forty interruptions. Everything reversible that already happened can still
be rolled back, newest first.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aven.core.messages import new_id
from aven.core.tools import Risk, Tool, ToolResult

State = Literal["applied", "pending", "committed", "undone", "discarded", "failed"]

_MARKS: dict[State, str] = {
    "applied": "✓",
    "pending": "⏸",
    "committed": "✓",
    "undone": "↩",
    "discarded": "✗",
    "failed": "!",
}


@dataclass
class Entry:
    """One thing that changed the world, or wants to."""

    tool: str
    args: dict[str, Any]
    preview: str
    risk: Risk
    state: State

    id: str = field(default_factory=new_id)
    ts: float = field(default_factory=time.time)
    output: str = ""

    # Reversible work carries its undo from the moment it ran. Pending work
    # carries the call itself, unfired, so commit() can still make it happen.
    undo: Callable[[], None] | None = None
    apply: Callable[[], ToolResult] | None = None

    # Which message this came out of. Rolling the world back and rolling the
    # conversation back are two halves of the same undo - Session.checkout()
    # takes it from here.
    origin_message_id: str | None = None


class Tray:
    """Holds the entries for one run and decides when they take effect."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []

    # -- during the run -----------------------------------------------------

    def execute(
        self, tool: Tool, args: dict[str, Any], origin_message_id: str | None = None
    ) -> tuple[str, bool]:
        """Run the call or stage it, and say what to tell the model.

        Returns (output_for_the_model, was_staged). A staged call is reported
        honestly: the model is told the action is waiting, so it stops planning
        around an effect that has not happened.
        """
        preview = tool.preview(args)

        if tool.risk == "read":
            # Nothing changed, so there is nothing to review or undo.
            return tool(**args).output, False

        if tool.risk == "irreversible":
            self.entries.append(
                Entry(
                    tool=tool.name,
                    args=dict(args),
                    preview=preview,
                    risk=tool.risk,
                    state="pending",
                    apply=lambda: tool(**args),
                    origin_message_id=origin_message_id,
                )
            )
            return f"staged, waiting for the user to approve: {preview}", True

        result = tool(**args)
        self.entries.append(
            Entry(
                tool=tool.name,
                args=dict(args),
                preview=preview,
                risk=tool.risk,
                state="applied",
                output=result.output,
                undo=result.undo,
                origin_message_id=origin_message_id,
            )
        )
        return result.output, False

    # -- afterwards ---------------------------------------------------------

    def pending(self) -> list[Entry]:
        return [e for e in self.entries if e.state == "pending"]

    def undoable(self) -> list[Entry]:
        return [e for e in self.entries if e.state in ("applied", "committed") and e.undo]

    def commit(self) -> list[Entry]:
        """Fire the staged calls. A failure stops the batch where it is.

        Half a batch is a bad outcome, but it is a truthful one. Carrying on
        past a failed send would leave the user believing all of it went out.
        """
        done: list[Entry] = []
        for entry in self.pending():
            try:
                result = entry.apply()
            except Exception as exc:
                entry.state = "failed"
                entry.output = f"{type(exc).__name__}: {exc}"
                break
            entry.state = "committed"
            entry.output = result.output
            entry.undo = result.undo
            done.append(entry)
        return done

    def discard(self) -> list[Entry]:
        """Drop everything still waiting. Nothing fires."""
        dropped = self.pending()
        for entry in dropped:
            entry.state = "discarded"
        return dropped

    def undo(self) -> list[Entry]:
        """Roll back what already ran, newest first.

        Order matters: moving a into b then b into c has to come apart in the
        opposite order, or the second undo looks for a file that is no longer
        where it was left.
        """
        rolled: list[Entry] = []
        for entry in reversed(self.undoable()):
            entry.undo()
            entry.state = "undone"
            rolled.append(entry)
        return rolled

    def rewind_point(self) -> str | None:
        """The message to check out to put the conversation back too."""
        touched = [e for e in self.entries if e.origin_message_id]
        return touched[0].origin_message_id if touched else None

    # -- showing it to a person --------------------------------------------

    def diff(self) -> str:
        """The batch as a person should see it before deciding."""
        if not self.entries:
            return "没有任何改动"

        pending, applied = self.pending(), self.undoable()
        lines = [f"待处理:{len(pending)} 项需批准 / {len(applied)} 项已执行可撤销"]
        for entry in self.entries:
            note = {"pending": "需要批准", "applied": "可撤销"}.get(entry.state, entry.state)
            lines.append(f"  {_MARKS[entry.state]}  {entry.preview}   [{note}]")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.entries)
