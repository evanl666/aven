"""aven as something other programs call.

The terminal renderer is for a person watching. These two are for a caller that
is not watching: a cron line, a shell script, a keyboard shortcut, another
agent. For a personal assistant that is not a side channel - most of what you
want an assistant to do, you want it to do without being sat with.

Both are clients of the same event stream the screen gets. Neither knows
anything the renderer does not.

One rule holds for both: **stdout carries the result, stderr carries the
commentary.** Which banner was printed, which files were read, how many tokens
it cost - all of that goes to stderr, so a caller can read stdout without
having to recognise our chatter in it.

The tray record at the end is not decoration. An irreversible action does not
happen unless something commits it, and in these modes nobody is there to press
a key, so a caller that does not check for pending entries would believe an
email was sent that is still sitting in the tray.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from aven.harness.events import AgentEnd, Event, MessageEnd, ToolEnd, TurnEnd
from aven.harness.messages import AssistantMessage
from aven.wire.protocol import detail_as_dict, entry_as_dict
from aven.wire.protocol import event_as_dict as as_json
from aven.harness.tx import Tray
from aven.text import t


class Jsonl:
    """Every event as one line of JSON on stdout."""

    def handle(self, event: Event) -> None:
        self._write(as_json(event))

    def close(self, tray: Tray, *, committed: int = 0) -> None:
        self._write(
            {
                "type": "tray",
                "committed": committed,
                "pending": [
                    entry_as_dict(e) for e in tray.pending()
                ],
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        # flush per line: a caller reading our stdout as a stream is doing so
        # because it wants each event when it happens, not when the pipe fills.
        json.dump(record, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()


def _did(event: ToolEnd) -> str:
    """One line saying what just ran, for somebody watching stderr.

    The preview, because it is the sentence a person would have been shown
    before approving - so it is already written to be read, and it is already
    short. Falls back to the tool's name when there is none.
    """
    said = event.preview or event.call.name
    if event.staged:
        return t("stream.doing_staged", what=said)
    if event.result.is_error:
        return t("stream.doing_failed", what=said)
    return t("stream.doing", what=said)


class Final:
    """Nothing on stdout until the end, then the answer and only the answer.

    Plus, on stderr, why the run ended if it did not end because the work was
    finished. Without that a caller cannot tell a finished job from one that
    gave up at a limit: both arrive as a confident paragraph on stdout, and the
    one that gave up stops mid-sentence about what it was going to do next.

    Found by running Terminal-Bench. Three hard tasks each ended at exactly
    twelve requests - the default turn limit - having said "Found it, let me
    add the printk" and then nothing. The limit was doing its job. Not saying
    so was the bug.
    """

    def __init__(self, *, trace: bool = True) -> None:
        self.text = ""
        self.turns = 0
        self.ended: str | None = None
        self.trace = trace

    def handle(self, event: Event) -> None:
        if isinstance(event, MessageEnd) and isinstance(event.message, AssistantMessage):
            # The last one that said something. A run can end on a reply that
            # is only tool calls, and printing an empty line is worse than
            # printing the sentence before it.
            if event.message.text:
                self.text = event.message.text
        elif isinstance(event, ToolEnd):
            # One line per call, on stderr, as it happens.
            #
            # Without this a one-shot run prints nothing at all until it ends,
            # and a run that never ends prints nothing ever. Four tasks in a
            # thirty-task benchmark sweep were killed by a wall clock after
            # twenty minutes each, and their logs hold the banner and then
            # nothing: no record of what had been tried, because the only
            # record was going to be written at the end.
            #
            # Deliberately not the result, only the call. The result is the
            # thing that is sometimes forty thousand characters long, and this
            # is a progress line, not a transcript - the session file is the
            # transcript.
            self._say(_did(event))
        elif isinstance(event, TurnEnd):
            self.turns = event.index + 1
        elif isinstance(event, AgentEnd):
            self.ended = event.reason

    def _say(self, line: str) -> None:
        # flush is belt-and-braces: sys.stderr has been line-buffered even when
        # it is a pipe since Python 3.9, so it is not what gets this out of a
        # process about to be killed. What matters is that the line is written
        # now rather than collected and printed at the end, which is what this
        # whole method exists to stop.
        if self.trace:
            print(line, file=sys.stderr, flush=True)

    def close(self, tray: Tray, *, committed: int = 0) -> None:
        if self.text:
            print(self.text)
        if self.ended == "max_turns":
            print(t("stream.max_turns", turns=self.turns), file=sys.stderr)
        elif self.ended == "truncated":
            print(t("stream.truncated"), file=sys.stderr)
        elif self.ended == "stalled":
            print(t("stream.stalled"), file=sys.stderr)
        for entry in tray.pending():
            print(t("stream.staged", preview=entry.preview), file=sys.stderr)

        # Flushed, because print to anything but a terminal is block-buffered and
        # a --watch run does not exit between turns. Found the hard way: a
        # triggered turn's answer sat in the buffer, and killing the watcher
        # discarded it rather than writing it late.
        sys.stdout.flush()
        sys.stderr.flush()
