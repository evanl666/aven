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

from aven.core.events import (
    AgentEnd,
    AgentStart,
    Event,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from aven.core.messages import AssistantMessage, ToolCall, to_dict
from aven.tx import Tray


def as_json(event: Event) -> dict[str, Any]:
    """One event as a plain JSON object.

    Written out by hand rather than with dataclasses.asdict: this is a wire
    format other people's scripts will match on, so the names have to be a
    decision and not a by-product of how the dataclasses happen to be spelled
    today.
    """
    match event:
        case AgentStart():
            return {"type": "agent_start", "prompt": event.prompt}
        case TurnStart():
            return {"type": "turn_start", "turn": event.index}
        case MessageDelta():
            return {"type": "message_delta", "text": event.text}
        case MessageEnd():
            return {"type": "message_end", "message": to_dict(event.message)}
        case ToolStart():
            return {"type": "tool_start", "call": _call(event.call)}
        case ToolEnd():
            return {
                "type": "tool_end",
                "call": _call(event.call),
                "result": to_dict(event.result),
                # True means it did not happen: the call is in the tray waiting
                # for a decision nobody is here to make.
                "staged": event.staged,
            }
        case TurnEnd():
            return {"type": "turn_end", "turn": event.index, "message": to_dict(event.message)}
        case AgentEnd():
            return {"type": "agent_end", "reason": event.reason}
    raise TypeError(f"no json form for {type(event).__name__}")


def _call(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "args": call.args}


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
                    {"preview": e.preview, "risk": e.risk} for e in tray.pending()
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
            print(f"未执行(等待确认):{entry.preview}", file=sys.stderr)
