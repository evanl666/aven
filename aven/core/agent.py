"""The agent loop.

A generator, not a callback API: the caller drives it with a `for`, sees every
step as it happens, and can stop simply by not asking for the next event. There
is no subscriber list, no risk of an exception in a renderer taking the loop
down with it, and a test reads as a plain list of what happened.

The loop owns exactly one thing - the order of operations. It does not know how
to talk to a model, what a tool does, or how any of it is displayed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence

from aven.core.events import (
    AgentEnd,
    AgentStart,
    Event,
    MessageEnd,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
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
from aven.tx import Tray

# Injected, never imported: the loop must stay runnable with no API key, and a
# test must be able to script a model's replies exactly.
ModelFn = Callable[[list[LlmMessage]], AssistantMessage]


def run(
    *,
    session: Session,
    prompt: str,
    model: ModelFn,
    tools: Sequence[Tool] | None = None,
    tray: Tray | None = None,
    max_turns: int = 12,
    source: str = "chat",
) -> Iterator[Event]:
    """Run one prompt to completion, yielding events as they happen.

    Every message appended to the session emits exactly one MessageEnd. Tool
    results additionally get a ToolEnd, so a renderer can show a finished tool
    without having to recognise tool results among the message stream.
    """
    by_name = {t.name: t for t in tools or ()}

    # No tray passed still means no irreversible action: one is created here and
    # its pending entries are simply never committed. Safe by omission.
    tray = tray if tray is not None else Tray()

    yield AgentStart(prompt=prompt)
    yield MessageEnd(message=session.append(UserMessage(text=prompt, source=source)))

    for index in range(max_turns):
        yield TurnStart(index=index)

        # history() flattens the tree to the current path - the branch the user
        # abandoned is in the file and not in this prompt.
        reply = model(to_llm(session.history()))
        session.append(reply)
        yield MessageEnd(message=reply)

        if not reply.tool_calls:
            yield TurnEnd(index=index, message=reply)
            yield AgentEnd(reason="end_turn")
            return

        for call in reply.tool_calls:
            yield ToolStart(call=call)
            message, staged = _execute(call, by_name, tray, origin=reply.id)
            session.append(message)
            yield MessageEnd(message=message)
            yield ToolEnd(call=call, result=message, staged=staged)

        yield TurnEnd(index=index, message=reply)

    # A model that keeps calling tools would otherwise run until the money is
    # gone. Stopping is a bug report, not a failure mode to hide.
    yield AgentEnd(reason="max_turns")


def _execute(
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
        output, staged = tray.execute(found, call.args, origin_message_id=origin)
    except Exception as exc:
        return failure(f"{type(exc).__name__}: {exc}")

    message = ToolResultMessage(
        tool_call_id=call.id, tool_name=call.name, output=output
    )
    return message, staged
