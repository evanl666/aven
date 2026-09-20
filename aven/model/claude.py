"""The Claude adapter - the only file in aven that imports a provider SDK.

It exists to satisfy one contract, `ModelFn`:

    list[LlmMessage] -> AssistantMessage

Everything provider-shaped stops here. The loop, the session, the tools and the
tray were written and tested for five steps without this file existing, and none
of them changed when it arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic

from aven.core.messages import AssistantMessage, LlmMessage, ToolCall
from aven.core.tools import Tool

# Opus 5. An assistant acting on someone's real files and real mail is the last
# place to save a few cents on a weaker model.
DEFAULT_MODEL = "claude-opus-5"

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
    """Running total for one session, so cost is visible while it accrues."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def __str__(self) -> str:
        return (
            f"{self.requests} requests · "
            f"in {self.input_tokens} (cached {self.cached_tokens}) · "
            f"out {self.output_tokens}"
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
        client: Any | None = None,
    ) -> None:
        # The client is injectable for the same reason the model function is:
        # the tests below run the whole adapter with no key and no network.
        self.client = client if client is not None else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.system = system
        self.tools = [t.for_model() for t in tools]
        self.usage = Usage()

    def __call__(self, messages: list[LlmMessage]) -> AssistantMessage:
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

        response = self.client.messages.create(**request)
        self.usage.add(response.usage)
        return to_assistant(response)


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
