"""Noticing that the agent has stopped getting anywhere.

A turn limit is a budget, not a judgement. It stops a loop eventually and it
cannot tell a model working through a hard problem from one going round in a
circle - both arrive at the limit, and only one of them was worth paying for.

Measured on a benchmark task that did exactly this: eighty calls, eighteen
distinct, one file written thirty-one times and one compile command run ten
times, ending at the turn cap with the task no closer than it was at call
twenty. The limit did its job. Nobody noticed in time for that to matter.

## What counts as not getting anywhere

The same call returning the same answer. That is it.

Keyed on the call AND its result, never the call alone. In that same run
`write /app/main.c.rs` appears thirty-one times, and every one of them wrote
different content - the preview is only the path. A detector keyed on calls
would fire on any edit loop, which is what working on a file looks like.

A command re-run after a real edit returns something different: a different
error, a different line number, a test that now passes. That is progress and
this says nothing about it. Identical output after identical input is the one
thing that cannot be progress.

## What this deliberately does not detect

The other way a run wastes its clock is drift: every action is new, and none of
them advances the task. A benchmark task spent its last fifteen turns writing
INDEX.md, START_HERE.md and QUICKSTART.md, then echoed "PROJECT COMPLETE".
Nothing repeated. Nothing helped.

Catching that means deciding whether an action advances the goal, which means
knowing the goal - a judgement about the quality of the model's plan. A harness
that makes it will sooner or later make it wrong and cut off a model that was
exploring. So this does not try. The honest limit of a mechanical check is
mechanical repetition, and a report that says "it stalled" when it means "I
disliked its plan" is worse than no report.

## Why it nudges before it stops

A false positive costs a sentence, not a task.

The remaining way to be wrong is a command whose output genuinely does not
change while the model works on something the command does not cover - a flaky
test printing the same failure, say, while real edits happen elsewhere. Telling
the model what has been observed lets it say "yes, I know, I am working on X",
and the run continues. Stopping on the first sign would end it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# How many recent calls are considered. Short enough that a stall is caught
# while the turns it would waste are still ahead, long enough that a two-step
# edit-and-check rhythm is not itself a window.
WINDOW = 12

# How many identical (call, result) pairs inside that window mean it. Three,
# not two: a command legitimately run twice around an edit that changed nothing
# relevant is ordinary, and a third says the model is not reading the answer.
REPEATS = 3

# What the model is told, once, the first time. Phrased as an observation and a
# choice rather than an instruction to stop, because the model may know
# something this does not - see the module docstring.
#
# English, like every string the model reads: instructions to a model are code,
# and they belong next to the logic they steer.
NUDGE = (
    "Something is not working. You have made this exact call {n} times now and "
    "had the identical result every time:\n\n"
    "    {what}\n\n"
    "Repeating it will return the same thing again. Either change approach - a "
    "different tool, a different command, reading something you have not read - "
    "or stop and say plainly what is blocking you and what you would need. "
    "Do not carry on with the same loop, and do not report the work as done."
)


@dataclass
class Watch:
    """Remembers recent calls and says when they have stopped telling us anything.

    One instance per run. Injected into the loop rather than built by it: what
    counts as progress is a judgement, and the loop's whole job is to have no
    judgements in it.
    """

    window: int = WINDOW
    repeats: int = REPEATS

    _recent: deque[tuple[str, str]] = field(default_factory=deque, init=False, repr=False)
    _said: bool = field(default=False, init=False, repr=False)

    def saw(self, name: str, args: dict, output: str) -> None:
        """Record one finished call.

        The arguments are rendered rather than hashed as objects so that two
        calls that differ only in dict ordering count as the same call, which
        they are.
        """
        self._recent.append((f"{name} {sorted(args.items())}", output))
        while len(self._recent) > self.window:
            self._recent.popleft()

    def stuck(self) -> tuple[str, int] | None:
        """The call that has stopped saying anything new, and how often.

        None while there is nothing to report. The window is cleared by
        `note`, not here, so that being told buys a clean slate: without that
        the next identical call would trip this again immediately and the run
        would end having given the model no chance to act on what it was told.
        """
        counted: dict[tuple[str, str], int] = {}
        for seen in self._recent:
            counted[seen] = counted.get(seen, 0) + 1

        worst = max(counted.items(), key=lambda pair: pair[1], default=None)
        if worst is None or worst[1] < self.repeats:
            return None
        return worst[0][0], worst[1]

    def told(self) -> bool:
        """Whether the model has already been told once this run."""
        return self._said

    def note(self, what: str, n: int) -> str:
        """The sentence to put in the conversation, and remember having put it."""
        self._said = True
        self._recent.clear()
        return NUDGE.format(n=n, what=what)
