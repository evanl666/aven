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

from collections.abc import Callable
from dataclasses import dataclass, field

from aven.core.calling import ModelFn, ask_model
from aven.core.messages import (
    AssistantMessage,
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

FILES_SEEN = """\

These files were acted on, in order. Keep the ones that still matter, by their \
exact paths:

{files}"""


# Argument names that carry a path. Tools name their arguments for the model to
# read, so these are the words that mean "a file" across the whole toolset.
PATH_ARGS = ("path", "src", "dst", "folder", "file")


def touched_files(messages: list[Message]) -> list[str]:
    """Every file the given stretch of conversation acted on, in order.

    Taken from the tool calls, not from what anyone said about them. A summary
    is prose and prose loses paths; this list is the part that must survive
    verbatim, because the next turn will ask for one of them by name.
    """
    seen: list[str] = []
    for message in messages:
        if isinstance(message, SummaryMessage):
            # Already-summarised stretches carry their own list. Compaction is
            # cumulative or it forgets the beginning of long tasks.
            for path in message.files:
                if path not in seen:
                    seen.append(path)
        elif isinstance(message, AssistantMessage):
            for call in message.tool_calls:
                for name in PATH_ARGS:
                    value = call.args.get(name)
                    if isinstance(value, str) and value and value not in seen:
                        seen.append(value)
    return seen


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

    # How to weigh the context. The default guesses from the characters; the
    # CLI passes one that reports what the provider actually counted, which is
    # exact and costs nothing because the number came back with the last reply.
    measure: Callable[[list[LlmMessage]], int] = field(default=estimate_tokens)

    def size(self, llm_messages: list[LlmMessage]) -> int:
        return self.measure(llm_messages) or estimate_tokens(llm_messages)

    def too_long(self, llm_messages: list[LlmMessage]) -> bool:
        return self.size(llm_messages) >= self.limit

    async def maybe_compact(
        self, session: Session, llm_messages: list[LlmMessage]
    ) -> SummaryMessage | None:
        """Summarise the old turns if the estimate says there are too many.

        Returns the summary it appended, or None when nothing was done - either
        the context still fits, or everything in it is too recent to drop.
        """
        if not self.too_long(llm_messages):
            return None
        return await self.compact_now(session)

    async def compact_now(
        self, session: Session, *, insist: bool = False
    ) -> SummaryMessage | None:
        """Summarise regardless of the estimate.

        `insist` separates two different situations. Compacting early because
        an estimate said so should not cost the turns the model is working on -
        the estimate is deliberately pessimistic and may simply be wrong. But
        once the provider has actually refused the request for length, there is
        nothing left to be careful about, so keep_turns is given up too: half a
        conversation beats a dead one.
        """
        path = session.history()

        for keep in ((self.keep_turns, 1) if insist else (self.keep_turns,)):
            old, _keep = split_at_turn(path, keep)
            if old:
                break
        else:
            # One turn, and it is already too long. Nothing here can be given
            # up without discarding the very thing being worked on.
            return None

        files = touched_files(old)
        instructions = INSTRUCTIONS
        if files:
            instructions += FILES_SEEN.format(files="\n".join(f"- {f}" for f in files))

        reply = await ask_model(
            self.model,
            to_llm(old) + [
                {"role": "user", "content": [{"type": "text", "text": instructions}]}
            ],
        )

        return session.append(
            SummaryMessage(text=reply.text, covers=[m.id for m in old], files=files)
        )
