"""Turns nobody typed.

`UserMessage.source` has listed `trigger:cron` and `trigger:fs` since the first
step and nothing has ever fired one. This is the clock and the eye.

What is here is only the deciding: given a set of triggers and what happened
last, which ones are due. Running the agent is the app's job, because the app is
what owns a session and a tray. That split is what makes this testable without a
clock - `due()` takes the time as an argument, so a test says "it is 08:31 now"
instead of waiting until it is.

Two kinds, because they are the two a desktop assistant actually needs:

- **at a time of day**, for "every morning, summarise what is coming"
- **when a folder changes**, for "something landed in Downloads"

Deliberately not: cron expressions. `at = "08:30"` covers what a person wants
from their own machine, and `*/5 9-17 * * 1-5` is a language to learn for cases
that have not come up.

The state - when each trigger last fired, and what a watched folder looked like -
is aven's, not the person's, so it lives beside the sessions rather than in the
file they edit.
"""

from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

WHERE = (".aven", "triggers.toml")
STATE = (".aven", "triggers.state.json")

# How long after its time a daily trigger will still fire. A machine that was
# asleep at 08:30 should still summarise the morning at 09:15; one that was off
# all day should not do it at midnight.
GRACE = 6 * 3600


@dataclass(frozen=True, kw_only=True)
class Trigger:
    """One reason to open a turn nobody typed."""

    name: str
    prompt: str

    # Exactly one of these. A trigger with neither never fires, which `load`
    # refuses rather than accepting quietly.
    at: str | None = None  # "08:30", local time
    watch: Path | None = None

    @property
    def source(self) -> str:
        """What the turn records about where it came from.

        The same strings UserMessage.source has always listed, so a session from
        a trigger is legible next to one somebody typed.
        """
        return "trigger:cron" if self.at is not None else "trigger:fs"

    def minutes(self) -> int | None:
        """`at` as minutes past midnight, or None if it is not a time."""
        if self.at is None:
            return None
        try:
            hours, _, mins = self.at.partition(":")
            total = int(hours) * 60 + int(mins)
        except ValueError:
            return None
        return total if 0 <= total < 24 * 60 else None


@dataclass
class Memory:
    """What aven remembers about firing, between runs."""

    fired: dict[str, float] = field(default_factory=dict)
    seen: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"fired": self.fired, "seen": self.seen}


def load(path: Path | None = None) -> list[Trigger]:
    """Read the triggers, or none.

    A malformed file yields nothing rather than raising, for the same reason the
    approvals file does: refusing to start because a convenience has a typo in it
    is the wrong trade, and doing nothing is the safe direction.
    """
    path = file_for() if path is None else Path(path).expanduser()
    if not path.is_file():
        return []

    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    triggers: list[Trigger] = []
    for n, row in enumerate(loaded.get("trigger", [])):
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt", "")).strip()
        at = str(row.get("at", "")).strip() or None
        watch = str(row.get("watch", "")).strip() or None

        # A trigger with no prompt has nothing to ask, and one with neither a
        # time nor a folder has no moment to ask it. Both are mistakes to drop
        # rather than shapes to support.
        if not prompt or (at is None) == (watch is None):
            continue

        trigger = Trigger(
            name=str(row.get("name", "")).strip() or f"trigger-{n + 1}",
            prompt=prompt,
            at=at,
            watch=Path(watch).expanduser() if watch else None,
        )
        if at is not None and trigger.minutes() is None:
            continue  # "8.30", "25:00" - not a time of day
        triggers.append(trigger)

    return triggers


def due(
    triggers: list[Trigger], memory: Memory, now: float | None = None
) -> list[Trigger]:
    """Which triggers should fire, and remember that they did.

    Takes the time rather than reading the clock, so a test can say "it is 08:31"
    instead of waiting until it is. Mutates `memory`: deciding and recording are
    one step on purpose, because a decision recorded a moment later is a decision
    that can fire twice if anything goes wrong in between.
    """
    now = time.time() if now is None else now
    ready: list[Trigger] = []

    for trigger in triggers:
        if trigger.at is not None:
            if _time_has_come(trigger, memory, now):
                memory.fired[trigger.name] = now
                ready.append(trigger)
            continue

        changed = _new_files(trigger, memory)
        if changed:
            memory.fired[trigger.name] = now
            ready.append(trigger)

    return ready


def _time_has_come(trigger: Trigger, memory: Memory, now: float) -> bool:
    """Whether a daily trigger is due, having not already fired today."""
    wanted = trigger.minutes()
    if wanted is None:
        return False

    local = time.localtime(now)
    minutes_now = local.tm_hour * 60 + local.tm_min
    if minutes_now < wanted:
        return False

    # Past its time, but how far past? A machine asleep at 08:30 should still
    # summarise the morning at 09:15; one that was off all day should not do it
    # at midnight.
    if (minutes_now - wanted) * 60 > GRACE:
        return False

    last = memory.fired.get(trigger.name)
    if last is None:
        return True

    # Once a day. Compared by calendar day rather than by elapsed seconds, so a
    # trigger at 08:30 does not drift later every morning.
    return time.localtime(last)[:3] != local[:3]


def _new_files(trigger: Trigger, memory: Memory) -> list[str]:
    """Files in the watched folder that were not there last time.

    Names, not modification times: a file being rewritten in place is not
    something landing in Downloads, and a folder full of files aven has already
    seen should not fire on every pass.
    """
    folder = trigger.watch
    if folder is None or not folder.is_dir():
        return []

    try:
        present = sorted(p.name for p in folder.iterdir() if not p.name.startswith("."))
    except OSError:
        return []

    before = memory.seen.get(trigger.name)
    memory.seen[trigger.name] = present

    if before is None:
        # First sight of the folder. Everything in it is "new", and firing on all
        # of it would mean a trigger added today reacts to files from last year.
        return []

    return [name for name in present if name not in set(before)]


# --- what aven remembers -----------------------------------------------------


def file_for() -> Path:
    """Where the triggers are. Resolved per call so a test can redirect home."""
    return Path.home().joinpath(*WHERE)


def state_for() -> Path:
    return Path.home().joinpath(*STATE)


def remember(path: Path | None = None) -> Memory:
    path = state_for() if path is None else Path(path).expanduser()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Memory()
    return Memory(fired=dict(loaded.get("fired", {})), seen=dict(loaded.get("seen", {})))


def keep(memory: Memory, path: Path | None = None) -> None:
    """Write the state back. Failing to is not worth stopping over.

    The cost of a lost write is one trigger firing twice, which for "summarise my
    morning" is a wasted request rather than a problem - and stopping the run
    instead would turn a full disk into a broken assistant.
    """
    path = state_for() if path is None else Path(path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(memory.to_dict(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
