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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from aven.harness.messages import new_id
from aven.harness.tools import Detail, Risk, Tool, ToolResult
from aven.harness.tx.policy import Policy
from aven.harness.tx.standing import Approval, Standing
from aven.text import t

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

    # The same thing said in a shape a window can draw, when the tool has one.
    # Always optional: `preview` is the line every surface can rely on, and a
    # renderer that needed `detail` would break on the tools that have none.
    detail: Detail | None = None

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

    # Set when a standing approval let this run without being asked. Recorded
    # rather than implied: a transcript has to show plainly that this was not a
    # fresh decision. See harness/tx/standing.py.
    approved_by: str | None = None


class Tray:
    """Holds the entries for one run and decides when they take effect."""

    def __init__(
        self, policy: Policy | None = None, standing: Standing | None = None
    ) -> None:
        self.entries: list[Entry] = []
        self.policy = policy

        # Decisions already made. Absent means every irreversible call waits,
        # which is what the tray did before there was such a thing and is the
        # right thing for it to do when nobody said otherwise.
        self.standing = standing or Standing()

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
        detail = tool.detail_for(args)

        # The tool's declaration is a floor. A policy sees what the tool cannot
        # - the arguments, and how much this run has already changed - and may
        # raise the call above it, never below.
        # Asked of the tool rather than read off it: a tool that runs whatever
        # it is handed decides per call. See Tool.risk_for.
        risk = tool.risk_for(args)
        verdict = self.policy.judge(tool, args, self.entries) if self.policy else None
        if verdict is not None:
            risk = verdict.risk
            preview = f"{preview}  ⚠ {verdict.reason}"

        if risk == "read":
            # Nothing changed, so there is nothing to review or undo.
            return tool(**args).output, False

        if risk == "irreversible":
            # A decision already made, checked against the preview - the same
            # sentence the person read when they made it.
            #
            # Never for a call the policy raised. A warning is this person's own
            # rule saying "this one is different", and an approval written before
            # there was a warning cannot have accounted for it. Worse, the
            # warning is appended, so the old wording is still a prefix of the
            # new one and a substring match would honour it silently. A raised
            # call is asked about, every time.
            granted = None if verdict is not None else self.standing.covering(tool, preview)
            if granted is None:
                self.entries.append(
                    Entry(
                        tool=tool.name,
                        args=dict(args),
                        preview=preview,
                        detail=detail,
                        risk=risk,
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
                    detail=detail,
                    risk=risk,
                    # Committed, not applied: it did happen, and it happened
                    # because a decision covered it rather than because it was
                    # safe to do unasked.
                    state="committed",
                    output=result.output,
                    undo=result.undo,
                    origin_message_id=origin_message_id,
                    approved_by=str(granted),
                )
            )
            return result.output, False

        result = tool(**args)
        self.entries.append(
            Entry(
                tool=tool.name,
                args=dict(args),
                preview=preview,
                detail=detail,
                risk=risk,
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

    def commit(self, only: Sequence[str] | None = None) -> list[Entry]:
        """Fire the staged calls. A failure stops the batch where it is.

        Half a batch is a bad outcome, but it is a truthful one. Carrying on
        past a failed send would leave the user believing all of it went out.

        `only` fires the named entries and leaves the rest pending. A batch with
        a purchase in it is not something anybody should have to accept whole to
        accept any of, and a checkbox beside each line needs somewhere to send
        the answer.
        """
        wanted = self.pending() if only is None else [
            e for e in self.pending() if e.id in set(only)
        ]
        done: list[Entry] = []
        for entry in wanted:
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
            return t("tray.empty")

        pending, applied = self.pending(), self.undoable()
        lines = [t("tray.summary", pending=len(pending), applied=len(applied))]
        for entry in self.entries:
            note = t(f"tray.state.{entry.state}") if entry.state in (
                "pending", "applied"
            ) else entry.state
            lines.append(f"  {_MARKS[entry.state]}  {entry.preview}   [{note}]")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.entries)
