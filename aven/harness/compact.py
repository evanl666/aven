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

from aven.harness.calling import ModelFn, ask_model
from aven.harness.messages import (
    AssistantMessage,
    ContextEdit,
    LlmMessage,
    Message,
    SummaryMessage,
    ToolResultMessage,
    UserMessage,
    to_llm,
)
from aven.harness.session import Session

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

CARRIED = """\


The <earlier_conversation> block above is a summary you wrote at an earlier \
compaction. The messages it describes are already gone, so it is the only \
record of them left. Carry its facts forward into the new summary rather than \
compressing it again - anything you drop from it is lost for good."""

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


def split_within_turn(messages: list[Message]) -> tuple[list[Message], list[Message]]:
    """Divide inside one turn, for when the turn is too long on its own.

    split_at_turn refuses to cut here, and refusing is right nearly always: a
    turn is the unit the model is working in. But a single turn can outgrow the
    window by itself - one request that reads twenty files - and then refusing
    leaves a conversation that cannot be shortened and so cannot go on at all.

    The cut lands immediately before an assistant message, which is the only
    other place it is safe. A tool result always follows the call it answers, so
    nothing above an assistant message is waiting on it; cutting there cannot
    separate a call from its result.
    """
    replies = [i for i, m in enumerate(messages) if isinstance(m, AssistantMessage)]
    if not replies or replies[-1] == 0:
        # Nothing has been said back yet, so there is no boundary inside the
        # turn - only the person's request, which is the one thing to keep.
        return [], messages

    cut = replies[-1]
    return messages[:cut], messages[cut:]


# A tool result longer than this is almost always a file read or a directory
# listing whose moment has passed. Kept turns keep their shape; they do not
# need to keep their bulk.
BULKY_RESULT = 2_000

LEAVING = """\
The conversation below is one attempt at a task. It is being set aside for \
another attempt, and you are writing down what the next one should know so the \
work is not repeated.

Write it for the assistant picking up the other attempt. Be specific about:

- What was tried, and what it found out - especially anything that turned out \
to be false, missing, or harder than it looked
- Concrete values it would otherwise have to look up again: paths, names, \
addresses, amounts
- What was actually changed on disk, and what was only proposed

Say what happened, not what should happen next - the other attempt has its own \
plan. No preamble."""


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
        old, keep = self._divide(path, insist=insist)
        if not old:
            return None

        files = touched_files(old)
        instructions = INSTRUCTIONS
        if any(isinstance(m, SummaryMessage) and m.scope == "earlier" for m in old):
            instructions += CARRIED
        if files:
            instructions += FILES_SEEN.format(files="\n".join(f"- {f}" for f in files))

        reply = await ask_model(
            self.model,
            to_llm(old) + [
                {"role": "user", "content": [{"type": "text", "text": instructions}]}
            ],
        )

        summary = session.append(
            SummaryMessage(text=reply.text, covers=[m.id for m in old], files=files)
        )
        self._trim(session, keep)
        return summary

    async def summarise_branch(
        self, session: Session, leaving: str
    ) -> SummaryMessage | None:
        """Write down what a branch found out, on the branch being entered.

        Moving between branches otherwise throws away everything the one you
        leave learned. Twenty file reads and three dead ends are on a path the
        next request no longer includes, so the new branch repeats them.

        Appended after the checkout, so it lands on the branch being entered and
        travels with it. It covers nothing and hides nothing - the messages it
        describes were never in this projection to begin with, which is the
        whole reason it is needed.
        """
        left = session.history(leaving)
        if not any(isinstance(m, AssistantMessage) for m in left):
            # Nothing was tried, so there is nothing to carry. A branch point
            # someone stepped onto and straight off again is the common case.
            return None

        reply = await ask_model(
            self.model,
            to_llm(left) + [{"role": "user", "content": [{"type": "text", "text": LEAVING}]}],
        )

        return session.append(
            SummaryMessage(text=reply.text, scope="branch", files=touched_files(left))
        )

    def _divide(
        self, path: list[Message], *, insist: bool
    ) -> tuple[list[Message], list[Message]]:
        """Pick where to cut, giving more up only when told to insist.

        Three attempts, each conceding something the one before it kept. An
        empty `old` from the last of them means there is genuinely nothing to
        summarise, and the caller has to report the overflow rather than fix it.
        """
        old, keep = split_at_turn(path, self.keep_turns)
        if old or not insist:
            return old, keep

        # keep_turns is a courtesy, and the provider has stopped accepting it.
        old, keep = split_at_turn(path, 1)
        if old:
            return old, keep

        # One turn, too long by itself. Cut inside it - the last thing said back
        # is what the next reply builds on, and the request survives in the
        # summary.
        return split_within_turn(path)

    def _trim(self, session: Session, keep: list[Message]) -> None:
        """Replace the bulk of old tool results in the kept turns with a stub.

        Summarising alone leaves the recent turns whole, and a single file read
        in them can outweigh everything that was just summarised away. The turn
        keeps its shape - the call, the result, the reply that followed - and
        loses only the payload nobody is going to read again. The original is
        one line away in the session file.

        The most recent turn is left whole: it is the one being worked in, and
        its results are what the next reply is most likely built from.

        So a `keep` with no user message in it - which is what a cut inside a
        single turn leaves - has nothing to trim. All of it is the turn being
        worked in, and stubbing it would take away the very results the reply
        after this one has to read.
        """
        turn_starts = [i for i, m in enumerate(keep) if isinstance(m, UserMessage)]
        older = keep[: turn_starts[-1]] if turn_starts else []

        for message in older:
            if not isinstance(message, ToolResultMessage):
                continue
            if len(message.output) <= BULKY_RESULT:
                continue
            session.append(
                ContextEdit(
                    target=message.id,
                    replacement=(
                        f"[{len(message.output)} characters from {message.tool_name}, "
                        f"dropped to make room. Run it again if it is still needed.]"
                    ),
                )
            )
