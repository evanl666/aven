"""Decisions this person already made, so they are not asked twice.

An assistant that asks about the fortieth identical filing move teaches people to
approve without reading, and once that is learned the tray protects nothing. The
answer is to record the decision, not to widen what runs unasked.

Everything here is arranged against the failure mode that arrangement invites.

**It is not the policy layer, and does not weaken it.** A policy rule may only
ever raise a call's risk; it speaks for the machine's judgement about what a call
is. An approval speaks for the person, and a person is allowed to have decided.
Keeping them in separate files is deliberate: the day someone is tempted to let a
rule lower a risk, the fact that approvals live somewhere else is the argument
against it.

**It is narrow by construction.** An approval names one tool and a fragment that
must appear in the preview. There is no wildcard for the tool, because "allow
everything" is not a decision anybody makes deliberately, and no empty fragment,
because a tool approved for all its arguments is the same thing said quietly.

**A tool may refuse.** `Tool.pre_approvable` is False for anything that cannot be
taken back or that spends money. No file can override it - a person cannot
pre-approve sending mail by editing a list, because the mail they would be
approving is one they have not read.

**Nothing is silent.** A pre-approved call is recorded as `approved_by` on the
entry, so a transcript shows plainly that it was not a fresh decision, and the
count in force is printed at the start of every run.

The file is the person's, read-only to aven, in the format everything else here
uses: they can see what they granted, and delete a line to take it back.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from aven.harness.tools import Tool

WHERE = (".aven", "approvals.toml")


@dataclass(frozen=True, kw_only=True)
class Approval:
    """One standing decision.

    `when` is matched against the preview - the same sentence the person read
    when they decided - rather than against the arguments. The preview is what
    they agreed to, so it is what the agreement should be checked against.
    """

    tool: str
    when: str
    note: str = ""

    def covers(self, tool: Tool, preview: str) -> bool:
        if not tool.pre_approvable:
            # Said by the tool, unreachable from the file. See the module
            # docstring: a person cannot approve a mail they have not read.
            return False
        if tool.name != self.tool:
            return False
        return self.when.lower() in preview.lower()

    def __str__(self) -> str:
        return f"{self.tool} where the preview contains {self.when!r}"


@dataclass(frozen=True)
class Standing:
    """Every approval in force, and the one question worth asking of them."""

    approvals: list[Approval] = field(default_factory=list)

    def covering(self, tool: Tool, preview: str) -> Approval | None:
        """The first approval that covers this call, or None.

        First rather than best: they are a person's own list in their own order,
        and a "most specific match" rule would mean the list no longer reads the
        way it behaves.
        """
        for approval in self.approvals:
            if approval.covers(tool, preview):
                return approval
        return None

    def __len__(self) -> int:
        return len(self.approvals)

    def __bool__(self) -> bool:
        return bool(self.approvals)


def read(path: Path | None = None) -> Standing:
    """Load the approvals, or none at all.

    A malformed file grants nothing rather than raising. Refusing to start
    because a list of conveniences has a typo in it would be the wrong trade -
    and granting nothing is the safe direction to fail in.
    """
    path = file_for() if path is None else Path(path).expanduser()
    if not path.is_file():
        return Standing()

    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Standing()

    approvals: list[Approval] = []
    for row in loaded.get("approve", []):
        if not isinstance(row, dict):
            continue
        tool = str(row.get("tool", "")).strip()
        when = str(row.get("when", "")).strip()
        # Both required. A missing tool is "anything" and a missing fragment is
        # "always", and neither is a decision somebody made on purpose.
        if not tool or not when:
            continue
        approvals.append(Approval(tool=tool, when=when, note=str(row.get("note", ""))))

    return Standing(approvals)


def file_for() -> Path:
    """Where the approvals live.

    Resolved per call rather than at import, so a test can redirect the home
    directory - and a test that cannot redirect it writes to the real one.
    """
    return Path.home().joinpath(*WHERE)
