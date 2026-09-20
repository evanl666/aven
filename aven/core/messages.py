"""Message types.

Two layers, deliberately kept apart:

  Message    - what aven stores in a session. Rich, app-specific, never lossy.
  LlmMessage - what the model actually sees. Derived, filtered, possibly rewritten.

The gap between them is where aven does its real work: hiding private values
behind handles, collapsing staged changes into a summary, dropping UI-only
noise. Never let these two collapse into one type.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Literal

# The wire format we normalise on internally is Anthropic's, because it is the
# most expressive of the mainstream tool-use formats. Other providers are
# adapted from this shape in the provider layer, not here.
LlmMessage = dict[str, Any]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(kw_only=True)
class BaseMessage:
    """Every message is a node in the session tree.

    `parent_id` is what makes branching free: to fork a conversation you just
    append a new child to an older node. Nothing is ever rewritten or deleted.
    """

    kind: ClassVar[str] = "base"

    id: str = field(default_factory=new_id)
    parent_id: str | None = None
    ts: float = field(default_factory=time.time)


@dataclass(kw_only=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = field(default_factory=new_id)


@dataclass(kw_only=True)
class UserMessage(BaseMessage):
    """A turn addressed to the agent.

    `source` matters: in aven a turn is not always typed by a human. A cron
    tick, a new mail, a file landing in ~/Downloads all open turns too, and the
    agent should be able to tell them apart.
    """

    kind: ClassVar[str] = "user"

    text: str
    source: str = "chat"  # chat | trigger:cron | trigger:fs | trigger:mail | ...


@dataclass(kw_only=True)
class AssistantMessage(BaseMessage):
    kind: ClassVar[str] = "assistant"

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "aborted", "error"] = "end_turn"


@dataclass(kw_only=True)
class ToolResultMessage(BaseMessage):
    kind: ClassVar[str] = "tool_result"

    tool_call_id: str
    tool_name: str
    output: str
    is_error: bool = False


@dataclass(kw_only=True)
class NoteMessage(BaseMessage):
    """UI-only. Recorded in the session, never shown to the model.

    This type exists mostly to prove the seam is real: if `to_llm` cannot drop a
    message, the two layers were never actually separate.
    """

    kind: ClassVar[str] = "note"

    text: str
    level: Literal["info", "warn", "error"] = "info"


Message = UserMessage | AssistantMessage | ToolResultMessage | NoteMessage


def to_llm(messages: list[Message]) -> list[LlmMessage]:
    """Project stored messages down to what the model sees.

    Two things happen here that are easy to get wrong:

    1. NoteMessage disappears entirely.
    2. Consecutive tool results are merged into one user message. Anthropic
       carries tool results under role "user", and a parallel tool batch must
       come back as one message with several tool_result blocks, not one
       message each.
    """
    out: list[LlmMessage] = []

    for msg in messages:
        if isinstance(msg, NoteMessage):
            continue

        if isinstance(msg, UserMessage):
            out.append({"role": "user", "content": [{"type": "text", "text": msg.text}]})

        elif isinstance(msg, AssistantMessage):
            content: list[dict[str, Any]] = []
            if msg.text:
                content.append({"type": "text", "text": msg.text})
            for call in msg.tool_calls:
                content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
                )
            if content:
                out.append({"role": "assistant", "content": content})

        elif isinstance(msg, ToolResultMessage):
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": msg.output,
            }
            if msg.is_error:
                block["is_error"] = True

            # Merge into the previous message if it is already a tool-result batch.
            if out and out[-1]["role"] == "user" and _is_tool_result_batch(out[-1]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})

    return out


def _is_tool_result_batch(llm_msg: LlmMessage) -> bool:
    content = llm_msg.get("content")
    return bool(content) and all(b.get("type") == "tool_result" for b in content)


# ---------------------------------------------------------------------------
# Serialisation
#
# Sessions are append-only JSONL: one message per line, never rewritten. That
# shapes both directions below - `to_dict` must be complete (nothing is stored
# anywhere else), and `from_dict` must tolerate lines written by a future
# version of aven, because old code will keep reading new files.
# ---------------------------------------------------------------------------

_KINDS: dict[str, type[BaseMessage]] = {
    cls.kind: cls
    for cls in (UserMessage, AssistantMessage, ToolResultMessage, NoteMessage)
}


def to_dict(msg: Message) -> dict[str, Any]:
    """Message -> a plain dict ready for json.dumps.

    `asdict` recurses into nested dataclasses, so `tool_calls` becomes a list of
    dicts for free. What it cannot do is emit `kind`: that is a ClassVar, not a
    field, so it never appears in `fields()` - we put it back by hand. Without
    it the line would be unreadable on the way in.
    """
    data = asdict(msg)
    data["kind"] = msg.kind
    return data


def from_dict(data: dict[str, Any]) -> Message:
    """A stored dict -> the message it came from.

    Unknown keys are dropped rather than raising. A session file outlives the
    code that wrote it; a field added in a later version must not make every
    older build crash on the whole history.
    """
    kind = data.get("kind")
    cls = _KINDS.get(kind)
    if cls is None:
        raise ValueError(f"unknown message kind: {kind!r}")

    known = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in known}

    if cls is AssistantMessage:
        kwargs["tool_calls"] = [ToolCall(**c) for c in data.get("tool_calls", [])]

    return cls(**kwargs)
