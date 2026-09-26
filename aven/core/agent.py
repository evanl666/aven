"""The agent loop.

An async generator, not a callback API: the caller drives it with an
`async for`, sees every step as it happens, and can stop simply by not asking
for the next event. There is no subscriber list, no risk of an exception in a
renderer taking the loop down with it, and a test reads as a plain list of what
happened.

Async because everything the loop waits on is someone else's - a model
streaming over the network, a subprocess talking to Calendar. A blocking wait
is invisible in a script and fatal in a UI, where it freezes the whole screen
including the key that would cancel it.

The loop owns exactly one thing - the order of operations. It does not know how
to talk to a model, what a tool does, or how any of it is displayed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

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
from aven.core.calling import ContextOverflow, ModelFn, stream_model
from aven.core.compact import Compactor
from aven.core.messages import (
    AssistantMessage,
    LlmMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)
from aven.core.session import Session
from aven.core.tools import Tool
from aven.core.toolbox import ToolSource, resolve
from aven.tx import Tray

# ModelFn is injected, never imported: the loop must stay runnable with no API
# key, and a test must be able to script a model's replies exactly.
__all__ = ["ModelFn", "run"]


async def run(
    *,
    session: Session,
    prompt: str,
    model: ModelFn,
    tools: ToolSource | None = None,
    tray: Tray | None = None,
    compactor: Compactor | None = None,
    max_turns: int = 12,
    source: str = "chat",
) -> AsyncIterator[Event]:
    """Run one prompt to completion, yielding events as they happen.

    Every message appended to the session emits exactly one MessageEnd. Tool
    results additionally get a ToolEnd, so a renderer can show a finished tool
    without having to recognise tool results among the message stream.
    """

    # No tray passed still means no irreversible action: one is created here and
    # its pending entries are simply never committed. Safe by omission.
    tray = tray if tray is not None else Tray()

    yield AgentStart(prompt=prompt)
    yield MessageEnd(message=session.append(UserMessage(text=prompt, source=source)))

    for index in range(max_turns):
        yield TurnStart(index=index)

        # Resolved each turn, not once: a tool the model brought in last turn
        # has to be callable this one.
        by_name = {t.name: t for t in resolve(tools)}

        # history() flattens the tree to the current path - the branch the user
        # abandoned is in the file and not in this prompt.
        llm_messages = to_llm(session.history())

        # Between turns is the only safe moment: every tool call has its result,
        # so a summary can stand in for a whole prefix without orphaning one.
        if compactor is not None:
            summary = await compactor.maybe_compact(session, llm_messages)
            if summary is not None:
                yield MessageEnd(message=summary)
                llm_messages = to_llm(session.history())

        reply: AssistantMessage | None = None
        for attempt in (1, 2):
            try:
                async for produced in _ask(model, llm_messages):
                    if isinstance(produced, AssistantMessage):
                        # An async generator cannot `return` a value the way a
                        # plain one can, so the finished message rides out as
                        # the last item.
                        reply = produced
                    else:
                        yield produced
                break
            except ContextOverflow:
                # The estimate was wrong and the provider said so. Shorten for
                # real and ask once more; a second refusal is the caller's to
                # see, since compacting again would only cost another summary.
                if attempt == 2 or compactor is None:
                    raise
                recovered = await compactor.compact_now(session, insist=True)
                if recovered is None:
                    raise
                yield MessageEnd(message=recovered)
                llm_messages = to_llm(session.history())

        assert reply is not None, "the model produced no message"
        session.append(reply)
        yield MessageEnd(message=reply)

        if not reply.tool_calls:
            yield TurnEnd(index=index, message=reply)

            if reply.stop_reason == "max_tokens":
                # The reply ran out of room mid-sentence. Until now that ended
                # the run looking like an ordinary finish, leaving the person
                # with half a thought and no reason for it.
                #
                # Compaction cannot undo the truncation - max_tokens is a cap
                # we set on the output, not a full window. It is still worth
                # doing: a context this long is why there was no room, and the
                # next turn needs some.
                if compactor is not None:
                    shortened = await compactor.maybe_compact(
                        session, to_llm(session.history())
                    )
                    if shortened is not None:
                        yield MessageEnd(message=shortened)
                yield AgentEnd(reason="truncated")
                return

            yield AgentEnd(reason="end_turn")
            return

        answered = 0
        try:
            for call in reply.tool_calls:
                yield ToolStart(call=call)
                message, staged = await _execute(call, by_name, tray, origin=reply.id)
                session.append(message)
                answered += 1
                yield MessageEnd(message=message)
                yield ToolEnd(call=call, result=message, staged=staged)
        except (asyncio.CancelledError, GeneratorExit):
            # Interrupted mid-batch - Esc in the UI, or a caller that stopped
            # asking for events. Every tool_use in the reply still needs a
            # tool_result, or the next request is a malformed conversation,
            # so close the unanswered ones before letting the interruption on.
            _close_unanswered(session, reply.tool_calls[answered:])
            raise

        yield TurnEnd(index=index, message=reply)

    # A model that keeps calling tools would otherwise run until the money is
    # gone. Stopping is a bug report, not a failure mode to hide.
    yield AgentEnd(reason="max_turns")


async def _ask(
    model: ModelFn, llm_messages: list[LlmMessage]
) -> AsyncIterator[Event | AssistantMessage]:
    """Call the model, turning whatever it produces into events.

    Yields MessageDelta as text arrives and the finished AssistantMessage last.
    Mixing the two in one stream is the price of async generators not having
    return values; the caller tells them apart by type.
    """
    async for item in stream_model(model, llm_messages):
        if isinstance(item, AssistantMessage):
            yield item
        else:
            yield MessageDelta(text=item)


def _close_unanswered(session: Session, calls: Sequence[ToolCall]) -> None:
    """Record an interruption as the result of every call that has none.

    The first of these may in fact have finished: a tool runs in a thread, and
    a thread cannot be cancelled, only abandoned. So the result says what is
    known - it was interrupted - and not that nothing happened. Anything it did
    that was reversible is already in the tray and can still be undone.
    """
    for call in calls:
        session.append(
            ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                output="interrupted by the user; it may or may not have taken effect",
                is_error=True,
            )
        )


async def _execute(
    call: ToolCall, tools: dict[str, Tool], tray: Tray, origin: str
) -> tuple[ToolResultMessage, bool]:
    """Hand one tool call to the tray, turning any failure into a result.

    Never raise out of here. A tool_use with no matching tool_result is a
    malformed conversation, so a crashed tool has to come back as an errored
    result the model can read and react to.

    The loop no longer decides whether a call runs - the tray does, from the
    tool's declared risk. All the loop passes on is which message the call came
    out of, so an undo can roll the conversation back to the same point.
    """

    def failure(output: str) -> tuple[ToolResultMessage, bool]:
        message = ToolResultMessage(
            tool_call_id=call.id, tool_name=call.name, output=output, is_error=True
        )
        return message, False

    found = tools.get(call.name)
    if found is None:
        return failure(f"no such tool: {call.name!r}")

    try:
        # Tools are ordinary blocking functions - shutil.move, osascript. Run
        # them off the event loop so a slow one cannot freeze the interface.
        output, staged = await asyncio.to_thread(
            tray.execute, found, call.args, origin
        )
    except Exception as exc:
        return failure(f"{type(exc).__name__}: {exc}")

    message = ToolResultMessage(
        tool_call_id=call.id, tool_name=call.name, output=output
    )
    return message, staged
