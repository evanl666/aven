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

from aven.harness.events import Event, MessageEnd
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


class Final:
    """Nothing on stdout until the end, then the answer and only the answer."""

    def __init__(self) -> None:
        self.text = ""

    def handle(self, event: Event) -> None:
        if isinstance(event, MessageEnd) and isinstance(event.message, AssistantMessage):
            # The last one that said something. A run can end on a reply that
            # is only tool calls, and printing an empty line is worse than
            # printing the sentence before it.
            if event.message.text:
                self.text = event.message.text

    def close(self, tray: Tray, *, committed: int = 0) -> None:
        if self.text:
            print(self.text)
        for entry in tray.pending():
            print(t("stream.staged", preview=entry.preview), file=sys.stderr)

        # Flushed, because print to anything but a terminal is block-buffered and
        # a --watch run does not exit between turns. Found the hard way: a
        # triggered turn's answer sat in the buffer, and killing the watcher
        # discarded it rather than writing it late.
        sys.stdout.flush()
        sys.stderr.flush()
