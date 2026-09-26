"""What the person typed while the agent was already working.

A run can take a minute, and in that minute they notice something: the wrong
folder, a file to leave alone, one more thing while you are in there. Without
somewhere to put that, the only honest answer is "wait, or press Esc" - and Esc
throws away work that was fine.

So typing while busy queues the text, and the loop picks it up at the two
moments where a new user message cannot break anything:

- **between turns**, once every tool call has its result. The model sees it on
  the very next request, which is what makes it steering rather than a reply to
  something already finished.
- **at the end of a run**, in place of stopping. A follow-up typed a second too
  late continues the same run instead of needing another Enter.

Nowhere else. Appending a user message between a tool call and its result would
hand the provider a malformed conversation, which is the one thing the
projection must never produce.

No lock: the queue is touched from the UI's event loop and from the loop's own
coroutine, and those are the same thread. A queue crossing threads would need
one, and this is the comment that should stop anyone assuming it does.
"""

from __future__ import annotations


class Steering:
    """A queue of messages typed while a run was in flight."""

    def __init__(self) -> None:
        self._queue: list[str] = []

    def add(self, text: str) -> None:
        """Queue something typed mid-run. Blank text is not a message."""
        text = text.strip()
        if text:
            self._queue.append(text)

    def drain(self) -> list[str]:
        """Take everything, leaving the queue empty.

        All of it, in the order it was typed. Feeding these in one at a time
        would let the model act on the first correction before reading the
        second, which is exactly how you delete the wrong folder twice.
        """
        queued, self._queue = self._queue, []
        return queued

    def waiting(self) -> int:
        return len(self._queue)

    def __bool__(self) -> bool:
        return bool(self._queue)

    def __repr__(self) -> str:
        return f"<Steering {len(self._queue)} waiting>"
