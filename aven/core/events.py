"""What the agent loop reports as it runs.

Events are the only thing the loop hands out. A terminal renders them, a test
asserts on them, the daemon forwards them over RPC - none of them reach into
the loop. That keeps the loop free of any opinion about how it is displayed.

They are frozen: an event is a record of something that already happened, so
nothing downstream should be able to edit it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aven.core.messages import AssistantMessage, Message, ToolCall, ToolResultMessage


@dataclass(frozen=True, kw_only=True)
class AgentStart:
    prompt: str


@dataclass(frozen=True, kw_only=True)
class TurnStart:
    index: int


@dataclass(frozen=True, kw_only=True)
class MessageDelta:
    """A piece of the assistant's text, as it arrives.

    Only emitted by models that stream. A model that returns a finished message
    produces none of these, and a renderer that ignores them still works.
    """

    text: str


@dataclass(frozen=True, kw_only=True)
class MessageEnd:
    """A message was completed and written to the session."""

    message: Message


@dataclass(frozen=True, kw_only=True)
class ToolStart:
    call: ToolCall


@dataclass(frozen=True, kw_only=True)
class ToolEnd:
    call: ToolCall
    result: ToolResultMessage

    # True when risk was irreversible and the call was put in the tray instead
    # of being made. The undo itself belongs to the tray, not to an event - one
    # owner, so nothing can roll back twice.
    staged: bool = False


@dataclass(frozen=True, kw_only=True)
class TurnEnd:
    index: int
    message: AssistantMessage


@dataclass(frozen=True, kw_only=True)
class AgentEnd:
    reason: Literal["end_turn", "max_turns", "truncated"]


Event = AgentStart | TurnStart | MessageDelta | MessageEnd | ToolStart | ToolEnd | TurnEnd | AgentEnd
