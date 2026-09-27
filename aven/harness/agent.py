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

from aven.harness.events import (
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
from aven.harness.calling import ContextOverflow, ModelFn, stream_model
from aven.harness.compact import Compactor
from aven.harness.messages import (
    AssistantMessage,
    LlmMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)
from aven.harness.session import Session
from aven.harness.steering import Steering
from aven.harness.tools import Tool
from aven.harness.toolbox import ToolSource, resolve
from aven.harness.tx import Tray

# ModelFn is injected, never imported: the loop must stay runnable with no API
# key, and a test must be able to script a model's replies exactly.
__all__ = ["ModelFn", "run"]

# What a truncated reply is asked, to get the rest of it. Phrased as an
# instruction rather than as the word "continue", which a model reads as a fresh
# request and answers by starting the whole thing over.
#
# English, like every string the model reads: instructions to a model are code,
# and they belong next to the logic they steer. A model answers in the language
# it was addressed in, whatever language it was instructed in.
RESUME = (
    "Your last reply was cut off by a length limit. Carry on from exactly where "
    "it stopped. Do not repeat any part of what you already said."
)


async def run(
    *,
    session: Session,
    prompt: str,
    model: ModelFn,
    tools: ToolSource | None = None,
    tray: Tray | None = None,
    compactor: Compactor | None = None,
    steering: Steering | None = None,
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

    # One truncated reply per run gets a second go. See RESUME below.
    resumed = False

    yield AgentStart(prompt=prompt)
    yield MessageEnd(message=session.append(UserMessage(text=prompt, source=source)))

    for index in range(max_turns):
        yield TurnStart(index=index)

        # Resolved each turn, not once: a tool the model brought in last turn
        # has to be callable this one.
        by_name = {t.name: t for t in resolve(tools)}

        # Anything typed while the last turn ran joins the conversation here,
        # and only here. Every tool call already has its result, so a user
        # message cannot land between a call and its answer.
        if steering is not None:
            for text in steering.drain():
                yield MessageEnd(
                    message=session.append(UserMessage(text=text, source="steering"))
                )

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
                # The reply ran out of room mid-sentence.
                #
                # Compaction cannot undo the truncation - max_tokens is a cap
                # we set on the output, not a full window. It is still worth
                # doing first: a context this long is why there was no room,
                # and the turn that finishes the answer needs some.
                if compactor is not None:
                    shortened = await compactor.maybe_compact(
                        session, to_llm(session.history())
                    )
                    if shortened is not None:
                        yield MessageEnd(message=shortened)

                if not resumed:
                    # Ask it to finish, once. Ending here leaves the person
                    # holding half a sentence and no way to get the rest short
                    # of retyping the request - and the expensive part, the
                    # thinking and the tool calls, is already paid for.
                    #
                    # Once, because a request whose answer needs more room than
                    # max_tokens allows would otherwise be truncated again and
                    # again, each round costing a full context.
                    resumed = True
                    yield MessageEnd(
                        message=session.append(UserMessage(text=RESUME, source="resume"))
                    )
                    continue

                yield AgentEnd(reason="truncated")
                return

            if steering:
                # Typed a second too late to steer, so it arrives as a
                # follow-up: carry on rather than stopping and making them
                # press Enter again for something already queued.
                continue

            yield AgentEnd(reason="end_turn")
            return

        runnable, cut_off = _split_truncated(reply)

        answered = 0
        try:
            for call in runnable:
                yield ToolStart(call=call)
                message, staged = await _execute(call, by_name, tray, origin=reply.id)
                session.append(message)
                answered += 1
                yield MessageEnd(message=message)
                found = by_name.get(call.name)
                yield ToolEnd(
                    call=call,
                    result=message,
                    staged=staged,
                    detail=found.detail_for(call.args) if found else None,
                )
        except (asyncio.CancelledError, GeneratorExit):
            # Interrupted mid-batch - Esc in the UI, or a caller that stopped
            # asking for events. Every tool_use in the reply still needs a
            # tool_result, or the next request is a malformed conversation,
            # so close the unanswered ones before letting the interruption on.
            _close_unanswered(session, [*runnable[answered:], *cut_off])
            raise

        for call in cut_off:
            yield ToolStart(call=call)
            message = session.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    output=(
                        "not run: the reply was cut off before this call was "
                        "finished, so its arguments may be incomplete. "
                        "Issue it again."
                    ),
                    is_error=True,
                )
            )
            yield MessageEnd(message=message)
            yield ToolEnd(call=call, result=message, staged=False)

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


def _split_truncated(reply: AssistantMessage) -> tuple[list[ToolCall], list[ToolCall]]:
    """Separate the calls that may be run from one that was cut off mid-write.

    A reply that stopped at max_tokens stopped in the middle of writing
    something, and if it was writing a tool call, that call is the last one in
    the reply. Its arguments are whatever had been written by then.

    Running it is the wrong move. A tool call is not a sentence that reads a
    little short when it is truncated - `delete_file` with a path that stopped
    early is a different call, not a smaller one, and the tray cannot tell the
    difference because the arguments look well-formed. So the last call of a
    truncated reply is never run. It comes back as a failed result, which the
    model can read and reissue, and every call before it is untouched: those
    were finished.
    """
    if reply.stop_reason != "max_tokens" or not reply.tool_calls:
        return reply.tool_calls, []
    return reply.tool_calls[:-1], reply.tool_calls[-1:]


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
