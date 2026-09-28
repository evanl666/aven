"""Calling a model, whatever shape it takes.

Three shapes are allowed, and this is the one place that sorts them out. A
model may return a finished AssistantMessage, which keeps a scripted test model
to one line; it may return an awaitable of one; or it may be an async generator
that streams text and carries the finished message on its final chunk.

The loop needs the streaming form so it can show words as they arrive.
Compaction needs only the finished message. Both go through here rather than
importing each other.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from aven.harness.messages import AssistantMessage, LlmMessage

ModelFn = Callable[[list[LlmMessage]], Any]


class ContextOverflow(Exception):
    """The request was refused for being longer than the model can read.

    Providers say this in their own words and their own error classes. The
    adapter translates; the loop only needs to know that shortening the
    conversation and asking again is worth a try.
    """


class Unreachable(Exception):
    """The provider could not be asked, and trying again would not help.

    A bad key, an exhausted account, no network. Separate from every other
    failure because it is not a bug and there is nothing for a traceback to
    tell anybody - the reader needs one sentence and the thing to go and fix.
    It is also the most likely way a first run ends, which is the worst moment
    to print a stack trace.
    """



async def stream_model(
    model: ModelFn, llm_messages: list[LlmMessage]
) -> AsyncIterator[str | AssistantMessage]:
    """Yield text as it arrives, then the finished message last."""
    produced = model(llm_messages)

    if inspect.isawaitable(produced):
        produced = await produced

    if isinstance(produced, AssistantMessage):
        yield produced
        return

    async for item in produced:
        yield item


async def ask_model(model: ModelFn, llm_messages: list[LlmMessage]) -> AssistantMessage:
    """Drive a model to the end and return only what it finally said."""
    reply: AssistantMessage | None = None
    async for item in stream_model(model, llm_messages):
        if isinstance(item, AssistantMessage):
            reply = item
    if reply is None:
        raise RuntimeError("the model produced no message")
    return reply
