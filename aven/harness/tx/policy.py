"""Judging a call by its arguments, not only by its tool.

A tool declares one risk for every call it will ever receive, which is the
honest thing a tool can know about itself. It is not everything worth knowing:
deleting a scratch file and deleting a folder of contracts are the same tool,
and the fortieth move in a row is not the first.

A policy sees the call - the tool, the arguments, and what this run has already
done - and may raise its risk. It may never lower one. A tool that called
itself irreversible knows something the policy does not, and the point of the
declaration is that it cannot be talked out of it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from aven.harness.tools import Risk, Tool
from aven.text import t

# Least to most severe. A policy's verdict is taken only when it sits above
# what the tool declared.
ORDER: tuple[Risk, ...] = ("read", "reversible", "irreversible")


@dataclass(frozen=True)
class Verdict:
    """What a rule thinks, and why - the reason is shown to the person."""

    risk: Risk
    reason: str


class Rule(Protocol):
    def __call__(
        self, tool: Tool, args: dict[str, Any], done: Sequence[Any]
    ) -> Verdict | None:
        """Return a verdict to raise this call's risk, or None to abstain."""


@dataclass
class Policy:
    """A list of rules, and the one verdict they add up to."""

    rules: list[Rule]

    def judge(
        self, tool: Tool, args: dict[str, Any], done: Sequence[Any]
    ) -> Verdict | None:
        """The most severe verdict above the tool's own, or None.

        Every rule is asked, rather than stopping at the first: a call can be
        both bulk and sensitive, and the person deciding should be told the
        worse of the two reasons.
        """
        worst: Verdict | None = None
        for rule in self.rules:
            verdict = rule(tool, args, done)
            if verdict is None:
                continue
            if ORDER.index(verdict.risk) <= ORDER.index(tool.risk):
                continue  # the tool already says at least this much
            if worst is None or ORDER.index(verdict.risk) > ORDER.index(worst.risk):
                worst = verdict
        return worst


def bulk(limit: int = 25) -> Rule:
    """Ask before the run changes more than `limit` things.

    Undo covers a mistake; it does not cover not noticing one. Past some
    number of changes nobody reads the list, and the number is lower than
    people expect.
    """

    def rule(tool: Tool, args: dict[str, Any], done: Sequence[Any]) -> Verdict | None:
        if tool.risk == "read":
            return None
        if len(done) < limit:
            return None
        return Verdict(
            risk="irreversible",
            reason=t("policy.bulk", n=len(done)),
        )

    return rule


def protect(*patterns: str) -> Rule:
    """Ask before writing to anything whose path matches one of these.

    Matched against the arguments as the model wrote them, which is where a
    name like ".ssh" or "contracts" appears. A tool that only reads is left
    alone.
    """
    wanted = tuple(p.lower() for p in patterns)

    def rule(tool: Tool, args: dict[str, Any], done: Sequence[Any]) -> Verdict | None:
        if tool.risk == "read":
            return None
        for value in args.values():
            if not isinstance(value, str):
                continue
            low = value.lower()
            for pattern in wanted:
                if pattern in low:
                    return Verdict(
                        risk="irreversible",
                        reason=t("policy.protect", pattern=pattern),
                    )
        return None

    return rule


def guard(*rules: Rule) -> Policy:
    return Policy(rules=list(rules))
