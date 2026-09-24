"""Compaction: trading old detail for room to keep going.

A long task eventually fills the context window, and the failure is abrupt -
the request is simply rejected. Compaction replaces the oldest stretch of the
conversation with a summary of it, so the turn after that still fits.

What makes this safe here is the append-only session. The summary is one more
message on the tree; nothing is edited, nothing is deleted, and the file still
holds every word. Only the projection handed to the model gets shorter, which
is exactly the seam to_llm was built for.
"""

from __future__ import annotations

from dataclasses import dataclass

from aven.core.calling import ModelFn, ask_model
from aven.core.messages import (
    LlmMessage,
    Message,
    SummaryMessage,
    UserMessage,
    to_llm,
)
from aven.core.session import Session

INSTRUCTIONS = """\
You are summarising the earlier part of a conversation between a person and \
their assistant, so the assistant can keep working after the detail is dropped.

Write it for the assistant to read, not for the person. Be specific where being \
vague would cost it a repeated question:

- What the person asked for, and anything they said about how they want it done
- Concrete values it will need again: paths, folder names, email addresses, \
amounts, dates, identifiers
- What has already been done, and what came of it
- Anything still waiting on the person's approval
- Anything that failed, and why

Leave out pleasantries and reasoning it no longer needs. No preamble - start \
with the summary itself."""


def estimate_tokens(llm_messages: list[LlmMessage]) -> int:
    """Roughly how much of the window this would take.

    Deliberately pessimistic. Counting for real costs an extra API round trip
    per turn, and being early is cheap while being late means a rejected
    request. ASCII runs about four characters to the token; CJK is closer to
    one, so each non-ASCII character is counted whole.
    """
    text = repr(llm_messages)
    ascii_chars = sum(1 for c in text if c.isascii())
    return ascii_chars // 4 + (len(text) - ascii_chars)


def split_at_turn(messages: list[Message], keep: int) -> tuple[list[Message], list[Message]]:
    """Divide the path into (summarise these, keep these verbatim).

    The cut lands immediately before a user message, so what is kept starts a
    turn. Cutting anywhere else could leave a tool result whose tool call was
    summarised away - a malformed conversation, which is the one thing the
    projection must never produce.
    """
    turn_starts = [i for i, m in enumerate(messages) if isinstance(m, UserMessage)]
    if len(turn_starts) <= keep:
        return [], messages

    cut = turn_starts[-keep]
    return messages[:cut], messages[cut:]


@dataclass
class Compactor:
    """Decides when the context is too long, and shortens it when it is."""

    model: ModelFn
    limit: int = 120_000
    keep_turns: int = 4

    def too_long(self, llm_messages: list[LlmMessage]) -> bool:
        return estimate_tokens(llm_messages) >= self.limit

    async def maybe_compact(
        self, session: Session, llm_messages: list[LlmMessage]
    ) -> SummaryMessage | None:
        """Summarise the old part of the path, if there is too much of it.

        Returns the summary it appended, or None when nothing was done - either
        the context still fits, or everything in it is too recent to drop.
        """
        if not self.too_long(llm_messages):
            return None

        path = session.history()
        old, _keep = split_at_turn(path, self.keep_turns)
        if not old:
            # Fewer turns than we promised to keep. Summarising them would buy
            # room by throwing away exactly what the model is working on.
            return None

        reply = await ask_model(
            self.model,
            to_llm(old) + [
                {"role": "user", "content": [{"type": "text", "text": INSTRUCTIONS}]}
            ],
        )

        return session.append(
            SummaryMessage(text=reply.text, covers=[m.id for m in old])
        )
