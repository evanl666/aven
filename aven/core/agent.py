"""The agent loop.

A generator, not a callback API: the caller drives it with a `for`, sees every
step as it happens, and can stop simply by not asking for the next event. There
is no subscriber list, no risk of an exception in a renderer taking the loop
down with it, and a test reads as a plain list of what happened.

The loop owns exactly one thing - the order of operations. It does not know how
to talk to a model, what a tool does, or how any of it is displayed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

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

# Injected, never imported: the loop must stay runnable with no API key, and a
# test must be able to script a model's replies exactly.
ModelFn = Callable[[list[LlmMessage]], AssistantMessage]

# Step 4 replaces this with a real tool protocol carrying preview/undo. For now
# a tool is just a callable that returns something printable.
ToolFn = Callable[..., object]


def run(
    *,
    session: Session,
    prompt: str,
    model: ModelFn,
    tools: dict[str, ToolFn] | None = None,
    max_turns: int = 12,
    source: str = "chat",
) -> Iterator[Event]:
    """Run one prompt to completion, yielding events as they happen.

    Every message appended to the session emits exactly one MessageEnd. Tool
    results additionally get a ToolEnd, so a renderer can show a finished tool
    without having to recognise tool results among the message stream.
    """
    tools = tools or {}

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
            result = session.append(_execute(call, tools))
            yield MessageEnd(message=result)
            yield ToolEnd(call=call, result=result)

        yield TurnEnd(index=index, message=reply)

    # A model that keeps calling tools would otherwise run until the money is
    # gone. Stopping is a bug report, not a failure mode to hide.
    yield AgentEnd(reason="max_turns")


def _execute(call: ToolCall, tools: dict[str, ToolFn]) -> ToolResultMessage:
    """Run one tool call, turning any failure into a result.

    Never raise out of here. A tool_use with no matching tool_result is a
    malformed conversation, so a crashed tool has to come back as an errored
    result the model can read and react to.
    """
    fn = tools.get(call.name)
    if fn is None:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            output=f"no such tool: {call.name!r}",
            is_error=True,
        )

    try:
        output = fn(**call.args)
    except Exception as exc:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            output=f"{type(exc).__name__}: {exc}",
            is_error=True,
        )

    return ToolResultMessage(
        tool_call_id=call.id, tool_name=call.name, output=str(output)
    )
