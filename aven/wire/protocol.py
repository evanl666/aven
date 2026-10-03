"""What aven looks like to another program.

One module, because a wire format defined in two places drifts and the second
definition becomes a lie. Everything a client parses is described here and
nowhere else: events, tray entries, previews, connectors.

Written out by hand rather than with `dataclasses.asdict`. These names are what
somebody else's code will match on, so they have to be a decision rather than a
by-product of how the dataclasses happen to be spelled today - renaming a field
and changing a public format should be two separate acts.

Nothing here does any I/O. That is the seam: the shapes can be tested without a
process, a pipe or a socket, and `rpc.py` is left with only the plumbing.
"""

from __future__ import annotations

from typing import Any

from aven.harness.connect import Connections
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
from aven.harness.messages import Message, ToolCall, to_dict
from aven.harness.sessions import Card
from aven.harness.toolbox import ToolBox
from aven.harness.tools import Body, Detail, Diff, Moves, Order
from aven.harness.tree import Node
from aven.harness.tx import Entry, Tray

# Bumped when a client that understood the old shape would misread the new one.
# Adding a field is not that; renaming or removing one is.
#
# 2: `connect` answers with `name` where it used to say `connected`. The word
#    was doing two jobs - naming the group, and saying whether a group is in
#    play - and a client reading the old one would now get a boolean.
VERSION = 2


# --- what a call would do ----------------------------------------------------


def detail_as_dict(detail: Detail | None) -> dict[str, Any] | None:
    """A detail, tagged with which shape it is.

    The tag is what lets a client switch on it without guessing from which keys
    happen to be present.
    """
    match detail:
        case None:
            return None
        case Diff():
            return {"kind": "diff", "path": detail.path,
                    "before": detail.before, "after": detail.after}
        case Body():
            return {"kind": "body", "title": detail.title, "text": detail.text}
        case Moves():
            return {"kind": "moves", "pairs": [list(p) for p in detail.pairs]}
        case Order():
            return {"kind": "order", "items": [list(i) for i in detail.items],
                    "total": detail.total, "where": detail.where,
                    "account": detail.account, "arrives": detail.arrives}
    raise TypeError(f"no wire form for {type(detail).__name__}")


def entry_as_dict(entry: Entry) -> dict[str, Any]:
    """One tray entry.

    `id` is the field this exists for. A checkbox has to name what it is
    approving, and until there was a second surface nothing needed to.

    `approved_by` is on the wire deliberately. A client that draws a list of
    what happened has to be able to show that one of them was not a fresh
    decision - hiding it would make the audit trail depend on which surface you
    happened to be looking at.
    """
    return {
        "id": entry.id,
        "tool": entry.tool,
        "preview": entry.preview,
        "detail": detail_as_dict(entry.detail),
        "risk": entry.risk,
        "state": entry.state,
        "output": entry.output,
        "ts": entry.ts,
        "approved_by": entry.approved_by,
        # Which tool call this was, so a surface that drew the call as it
        # happened knows which row to change when it is decided.
        "call_id": entry.call_id,
        # Whether a checkbox may offer "don't ask again" for this one. False for
        # anything that spends money or cannot be recalled.
        "can_undo": entry.undo is not None,
    }


def tray_as_dict(tray: Tray) -> dict[str, Any]:
    """The whole tray: what waits, what can be taken back."""
    return {
        "pending": [entry_as_dict(e) for e in tray.pending()],
        "undoable": [entry_as_dict(e) for e in tray.undoable()],
    }


# --- the event stream --------------------------------------------------------


def event_as_dict(event: Event) -> dict[str, Any]:
    """One event from the loop.

    A new event type raises here rather than being dropped, so it fails in our
    tests instead of in somebody's parser.
    """
    match event:
        case AgentStart():
            return {"type": "agent_start", "prompt": event.prompt}
        case TurnStart():
            return {"type": "turn_start", "turn": event.index}
        case MessageDelta():
            return {"type": "message_delta", "text": event.text}
        case MessageEnd():
            return {"type": "message_end", "message": to_dict(event.message)}
        case ToolStart():
            return {"type": "tool_start", "call": call_as_dict(event.call)}
        case ToolEnd():
            return {
                "type": "tool_end",
                "call": call_as_dict(event.call),
                "result": to_dict(event.result),
                "preview": event.preview,
                "detail": detail_as_dict(event.detail),
                # True means it did NOT happen: the call is in the tray waiting
                # for a decision.
                "staged": event.staged,
            }
        case TurnEnd():
            return {"type": "turn_end", "turn": event.index,
                    "message": to_dict(event.message)}
        case AgentEnd():
            return {"type": "agent_end", "reason": event.reason}
    raise TypeError(f"no wire form for {type(event).__name__}")


def call_as_dict(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "args": call.args}


def usage_as_dict(usage: Any) -> dict[str, Any]:
    """What a turn cost, as numbers and as a sentence.

    Both, because they are read in different places. `line` is the sentence the
    terminal prints, already translated. The numbers are for a client that has
    to fit the same information somewhere narrow, or wants to draw it rather
    than print it - a 68px sidebar cannot take a sentence, and a window that
    regexed the numbers back out of a translated string would break the first
    time the wording changed.
    """
    return {
        "requests": getattr(usage, "requests", 0),
        "input": getattr(usage, "total_input", 0),
        "output": getattr(usage, "output_tokens", 0),
        "cached": getattr(usage, "cached_tokens", 0),
        "line": str(usage) if usage is not None else "",
    }


# --- what a client needs to draw itself --------------------------------------


def connectors_as_dict(
    box: ToolBox,
    describe: dict[str, str],
    connections: Connections | None = None,
) -> list[dict[str, Any]]:
    """The tool groups, as the connections pane draws them.

    Two things are being reported and they are not the same, which is why there
    are two fields rather than one flag:

        state       whether it *could* work - signed in, or with nothing to sign
                    in to. This outlives the conversation.
        connected   whether its tools are in front of the model right now. This
                    is per conversation, and is what the token saving was always
                    about.

    A service can be signed in and not connected: the credential is in the
    keychain, and this particular conversation has not needed it.

    `tools` is listed so somebody deciding whether to connect something can see
    what it would then be able to do. A group that is a plain list costs nothing
    to ask, so it is asked whether or not it is in play. A group that is a
    function is only asked once it is already in play: resolving it is what
    starts an MCP server process, and six configured servers would otherwise
    mean six processes launched by opening a settings pane.
    """
    listed = []
    for name in box.groups:
        connector = connections.get(name) if connections else None
        here = name in box.brought_in
        cheap = not callable(box.groups[name])
        listed.append({
            "name": name,
            "about": describe.get(name, "") or (connector.about if connector else ""),
            "state": connections.state(name) if connections else "ready",
            "connected": here,
            "tools": [tool.name for tool in box.tools_in(name)] if (here or cheap) else [],
            # Where the credential for this would be kept, so the answer to
            # "where does my token go" is on the screen that asks for it rather
            # than in the documentation.
            "keeps": (
                connector.auth.about()
                if connector is not None and connector.auth is not None
                else ""
            ),
            "trouble": connector.trouble if connector else None,
        })
    return listed


def card_as_dict(card: Card) -> dict[str, Any]:
    return {
        "path": str(card.path),
        "name": card.name,
        "title": card.title,
        "opening": card.opening,
        "messages": card.messages,
        "when": card.when,
    }


def node_as_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.message.id,
        "kind": node.message.kind,
        "text": getattr(node.message, "text", ""),
        "depth": node.depth,
        "on_path": node.on_path,
        "is_head": node.is_head,
    }


def transcript_as_dict(messages: list[Message]) -> list[dict[str, Any]]:
    """The conversation, for a client that just connected.

    Stored messages rather than the projection: a window shows what happened,
    including the notes and summaries `to_llm` hides from the model.
    """
    return [to_dict(message) for message in messages]


# --- responses ---------------------------------------------------------------


def ok(command: str, data: dict[str, Any] | None = None, *, id: str | None = None):
    record: dict[str, Any] = {"type": "response", "command": command, "ok": True}
    if id is not None:
        record["id"] = id
    record["data"] = data or {}
    return record


def failed(command: str, error: str, *, id: str | None = None):
    record: dict[str, Any] = {
        "type": "response", "command": command, "ok": False, "error": error
    }
    if id is not None:
        record["id"] = id
    return record
