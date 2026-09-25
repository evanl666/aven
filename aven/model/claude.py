"""The Claude adapter - the only file in aven that imports a provider SDK.

It exists to satisfy one contract, `ModelFn`:

    list[LlmMessage] -> AssistantMessage

Everything provider-shaped stops here. The loop, the session, the tools and the
tray were written and tested for five steps without this file existing, and none
of them changed when it arrived.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from aven.core.calling import ContextOverflow
from aven.core.messages import AssistantMessage, LlmMessage, ToolCall
from aven.core.tools import Tool

# Opus 5. An assistant acting on someone's real files and real mail is the last
# place to save a few cents on a weaker model.
DEFAULT_MODEL = "claude-opus-5"

# Used only when the Models API cannot be reached. Small enough that assuming
# it costs an early compaction rather than a rejected request.
ASSUMED_WINDOW = 200_000

# What a refusal for length looks like in the message body. Matched as text
# because the status code alone (400) covers every malformed request, and a
# genuine bad request must not be mistaken for something compaction can fix.
_TOO_LONG = ("prompt is too long", "too many tokens", "context window", "maximum context")

# Anthropic's stop reasons mapped onto ours. Anything unrecognised becomes
# "error" rather than being quietly treated as a normal finish.
_STOP_REASONS = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


@dataclass
class Usage:
    """Running total for one session, so cost is visible while it accrues.

    Cached tokens are counted apart because they are the cheap ones: an agent
    loop resends the whole conversation every turn, so on a long task most of
    the input should be a cache read, and a hit rate near zero means something
    upstream is changing bytes it should not.
    """

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    written_tokens: int = 0

    # What the last request actually weighed, as the provider counted it. The
    # totals above accumulate; this one is the only number that says how full
    # the window is right now.
    last_input: int = 0

    def add(self, usage: Any) -> None:
        self.requests += 1
        went_in = (
            (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        )
        self.last_input = went_in
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.written_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def total_input(self) -> int:
        """Every input token, however it was billed.

        `input_tokens` counts only what was neither read from nor written to
        the cache, and with caching on that is almost nothing - so it is not
        the number to show a person.
        """
        return self.input_tokens + self.cached_tokens + self.written_tokens

    @property
    def hit_rate(self) -> float:
        """Share of input served from cache, at a tenth of the price.

        Written tokens belong in the denominator: a turn that filled the cache
        paid 1.25x for those, and calling that a hit would make the first turn
        of every session look free.
        """
        return self.cached_tokens / self.total_input if self.total_input else 0.0

    def __str__(self) -> str:
        if self.total_input == 0:
            cache = "无缓存"
        else:
            cache = f"缓存读 {self.cached_tokens} · 写 {self.written_tokens}"
        return (
            f"{self.requests} 次请求 · "
            f"输入 {self.total_input}({cache}) · "
            f"输出 {self.output_tokens}"
        )


class Claude:
    """A ModelFn bound to a client, a tool set and a system prompt."""

    def __init__(
        self,
        *,
        tools: tuple[Tool, ...] | list[Tool] = (),
        system: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 8000,
        cache: bool = True,
        client: Any | None = None,
    ) -> None:
        # The client is injectable for the same reason the model function is:
        # the tests below run the whole adapter with no key and no network.
        self.client = client if client is not None else anthropic.AsyncAnthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.system = system
        self.cache = cache

        # Order matters and must never vary: the request is assembled
        # tools -> system -> messages, and caching is a prefix match, so one
        # reordered tool invalidates every turn that follows.
        self.tools = [t.for_model() for t in tools]
        self.usage = Usage()
        self._window: int | None = None

    async def __call__(
        self, messages: list[LlmMessage]
    ) -> AsyncIterator[str | AssistantMessage]:
        """Stream the reply: text as it arrives, then the finished message.

        An async generator, so nothing is sent until the loop asks for the
        first chunk. Only text is streamed - thinking is kept for replay, not
        shown, and a tool call is not something to type out character by
        character. The AssistantMessage comes last because an async generator
        has nowhere else to put a return value.
        """
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            # Adaptive is the default on Opus 5 anyway; saying it out loud keeps
            # this file readable when the default changes.
            "thinking": {"type": "adaptive"},
        }
        if self.system:
            request["system"] = self.system
        if self.tools:
            request["tools"] = self.tools
        if self.cache:
            # Top-level caching marks the last cacheable block, which in an
            # agent loop is the end of the conversation so far. Next turn that
            # whole prefix - tools, system and every message already sent - is
            # a cache read at a tenth of the price. Anything volatile in the
            # system prompt (a timestamp, a uuid) would silently defeat it.
            request["cache_control"] = {"type": "ephemeral"}

        # Streaming is also what keeps a long reply from hitting the SDK's
        # request timeout, so this is not only a matter of how it looks.
        try:
            async with self.client.messages.stream(**request) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
                complete = await stream.get_final_message()
        except anthropic.BadRequestError as refusal:
            if any(hint in str(refusal).lower() for hint in _TOO_LONG):
                raise ContextOverflow(str(refusal)) from refusal
            raise

        self.usage.add(complete.usage)
        yield to_assistant(complete)


    async def context_window(self) -> int:
        """How much this model can read, asked once and remembered.

        Hard-coding it means every new model is wrong in one direction or the
        other, and the direction that matters - assuming more room than there
        is - ends in a rejected request. The Models API knows; a failure to
        reach it falls back to the smaller assumption.
        """
        if self._window is None:
            try:
                info = await self.client.models.retrieve(self.model)
                self._window = int(getattr(info, "max_input_tokens", 0)) or ASSUMED_WINDOW
            except Exception:
                self._window = ASSUMED_WINDOW
        return self._window


def to_assistant(response: Any) -> AssistantMessage:
    """Split one API response into the three things we keep.

    Text and tool calls are ours to read. Thinking blocks are not - they are
    kept whole so the next request can hand them straight back.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    thinking: list[dict[str, Any]] = []

    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            text_parts.append(block.text)
        elif kind == "tool_use":
            # Reuse the provider's id: the tool_result we send back has to
            # quote it exactly.
            tool_calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input)))
        elif kind in ("thinking", "redacted_thinking"):
            thinking.append(_as_dict(block))

    return AssistantMessage(
        text="\n".join(text_parts),
        tool_calls=tool_calls,
        thinking=thinking,
        stop_reason=_STOP_REASONS.get(getattr(response, "stop_reason", None), "error"),
    )


def _as_dict(block: Any) -> dict[str, Any]:
    """SDK blocks are pydantic models; the wire wants plain dicts."""
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(block)
