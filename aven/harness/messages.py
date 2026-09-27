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

# How many of a summary's paths to replay. Enough that a long task keeps the
# names it will be asked for; few enough that the summary stays a summary.
SUMMARY_FILES = 40


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
    stop_reason: Literal[
        "end_turn", "tool_use", "max_tokens", "refusal", "aborted", "error"
    ] = "end_turn"

    # Provider blocks we store without understanding them. Claude reasons before
    # calling a tool, and the next request has to carry that reasoning back
    # verbatim or the call is rejected. Opaque in, opaque out - never edited,
    # never shown to the user.
    thinking: list[dict[str, Any]] = field(default_factory=list)


@dataclass(kw_only=True)
class ToolResultMessage(BaseMessage):
    kind: ClassVar[str] = "tool_result"

    tool_call_id: str
    tool_name: str
    output: str
    is_error: bool = False


@dataclass(kw_only=True)
class SummaryMessage(BaseMessage):
    """What a stretch of the conversation amounted to.

    Appended like anything else - the messages it stands for are still in the
    file, still on the path, still readable. `covers` only decides what is left
    out of the projection sent to the model. Compaction is lossy for the model
    and lossless on disk.

    Two things get summarised, and `scope` is the difference:

    "earlier" is compaction. It stands in for messages that are on this path,
    it hides them, and it leads the projection because it describes what came
    before everything still in it.

    "branch" is what was tried on a path that was left. Those messages are on
    another branch and were never in this projection, so it hides nothing and
    covers nothing - it only adds. It stays where it was appended, because that
    is when it was learned: reading it first would claim the work came before a
    conversation it actually came after.
    """

    kind: ClassVar[str] = "summary"

    text: str
    covers: list[str] = field(default_factory=list)
    scope: Literal["earlier", "branch"] = "earlier"

    # Files the summarised stretch touched, carried forward so the list
    # survives being summarised again. Read out of the tool calls rather than
    # out of the summary text: a model writing prose will drop a path it
    # considers incidental, and the one it drops is the one asked for next.
    files: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class ContextEdit(BaseMessage):
    """An append-only edit of one earlier message's contribution to context.

    The target is untouched: still on the path, still in the file, still what
    the tree navigates through and what an export contains. Only the projection
    handed to the model changes. Because the edit is itself a node, it is
    branch-relative for free - check out a point before it and the original
    contribution is back.

    `replacement` of None drops the target. A string stands in for its content.
    Compaction is the same idea done wholesale, which is why a SummaryMessage
    carries a list of ids rather than one.
    """

    kind: ClassVar[str] = "context_edit"

    target: str
    replacement: str | None = None


@dataclass(kw_only=True)
class NoteMessage(BaseMessage):
    """UI-only. Recorded in the session, never shown to the model.

    This type exists mostly to prove the seam is real: if `to_llm` cannot drop a
    message, the two layers were never actually separate.
    """

    kind: ClassVar[str] = "note"

    text: str
    level: Literal["info", "warn", "error"] = "info"


@dataclass(kw_only=True)
class MetaMessage(BaseMessage):
    """Something true of the session rather than said in it.

    A name, for now. It is an entry and not a sidecar file because that is how
    everything else here works: the file only grows, the last one on the path
    wins, and renaming is therefore undone by checking out an earlier point.
    Invisible to the model - what you called this conversation is not part of it.
    """

    kind: ClassVar[str] = "meta"

    name: str = ""


Message = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | SummaryMessage
    | ContextEdit
    | NoteMessage
    | MetaMessage
)


def to_llm(messages: list[Message]) -> list[LlmMessage]:
    """Project stored messages down to what the model sees.

    Five things happen here that are easy to get wrong:

    1. NoteMessage and MetaMessage disappear entirely.
    2. A SummaryMessage of scope "earlier" hides every message it covers, and
       leads what is left. It was appended after them, because the file only
       ever grows, but it describes what came before and has to be read that
       way. One of scope "branch" hides nothing and stays where it is: it
       describes another branch, and was learned at the point it sits.
    3. A ContextEdit drops or rewrites the one message it targets. Later edits
       on the path win, so an edit can be taken back by another edit.
    4. Thinking blocks lead the assistant turn, in their original order.
    5. Consecutive tool results are merged into one user message. Anthropic
       carries tool results under role "user", and a parallel tool batch must
       come back as one message with several tool_result blocks, not one
       message each.

    Nothing here alters `messages`. Every one of them is still on the path and
    still in the file; this is only the view handed to the model.
    """
    hidden, replaced, summaries = _edits(messages)
    hidden |= _orphaned_results(messages, hidden | set(replaced))

    # A later summary may cover an earlier one, so at most one survives.
    out: list[LlmMessage] = [
        _as_earlier_conversation(s)
        for s in summaries
        if s.scope == "earlier" and s.id not in hidden
    ]

    for msg in messages:
        if msg.id in hidden or isinstance(msg, (NoteMessage, MetaMessage, ContextEdit)):
            continue

        if isinstance(msg, SummaryMessage):
            # "earlier" already led the projection above.
            if msg.scope == "branch":
                out.append(_as_abandoned_branch(msg))
            continue

        stands_in = replaced.get(msg.id)

        if isinstance(msg, UserMessage):
            out.append(_text("user", stands_in if stands_in is not None else msg.text))

        elif isinstance(msg, AssistantMessage):
            if stands_in is not None:
                # Only text survives a rewrite. Any tool calls it made are gone,
                # which is why their results were hidden above.
                out.append(_text("assistant", stands_in))
                continue

            # Thinking first: the provider requires the original order back.
            content: list[dict[str, Any]] = list(msg.thinking)
            if msg.text:
                content.append({"type": "text", "text": msg.text})
            for call in msg.tool_calls:
                content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.args}
                )
            if content:
                out.append({"role": "assistant", "content": content})

        elif isinstance(msg, ToolResultMessage):
            # A rewritten result stays a tool_result: the call it answers is
            # still in the transcript and still needs answering.
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": stands_in if stands_in is not None else msg.output,
            }
            if msg.is_error and stands_in is None:
                block["is_error"] = True

            # Merge into the previous message if it is already a tool-result batch.
            if out and out[-1]["role"] == "user" and _is_tool_result_batch(out[-1]):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})

    return out


def _edits(
    messages: list[Message],
) -> tuple[set[str], dict[str, str], list[SummaryMessage]]:
    """Work out what is hidden, what stands in for what, and which summaries lead.

    One pass, because an edit is appended after the message it targets and a
    summary after everything it covers. Reading in order means the last edit on
    a target is the one still standing when the pass ends.
    """
    hidden: set[str] = set()
    replaced: dict[str, str] = {}
    summaries: list[SummaryMessage] = []

    for msg in messages:
        if isinstance(msg, SummaryMessage):
            hidden |= set(msg.covers)
            summaries.append(msg)
        elif isinstance(msg, ContextEdit):
            if msg.replacement is None:
                hidden.add(msg.target)
                replaced.pop(msg.target, None)
            else:
                replaced[msg.target] = msg.replacement
                hidden.discard(msg.target)

    return hidden, replaced, summaries


def _orphaned_results(messages: list[Message], gone: set[str]) -> set[str]:
    """Results whose call went with its assistant turn.

    An edit that drops or rewrites an assistant message takes its tool calls
    with it. Leaving the results behind would put a tool_result in the
    transcript answering nothing, which the API rejects - the same invariant
    the loop protects when a run is interrupted.
    """
    unanswered = {
        call.id
        for msg in messages
        if isinstance(msg, AssistantMessage) and msg.id in gone
        for call in msg.tool_calls
    }
    if not unanswered:
        return set()

    return {
        msg.id
        for msg in messages
        if isinstance(msg, ToolResultMessage) and msg.tool_call_id in unanswered
    }


def _text(role: str, text: str) -> LlmMessage:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _as_earlier_conversation(summary: SummaryMessage) -> LlmMessage:
    """Render a summary for the model, with the paths it stands for.

    The prose alone is not enough. A summariser writing prose drops a path it
    judges incidental, and that is the one the next turn asks for by name - so
    the paths are replayed beside the prose rather than trusted to it. Only the
    most recent survive a long task, since the list grows without bound and the
    oldest are the least likely to come up again.
    """
    text = summary.text
    if summary.files:
        shown = summary.files[-SUMMARY_FILES:]
        listed = "\n".join(f"- {path}" for path in shown)
        dropped = len(summary.files) - len(shown)
        if dropped:
            listed = f"[{dropped} earlier paths not listed]\n{listed}"
        text = f"{text}\n\nFiles this covers:\n{listed}"

    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"<earlier_conversation>\n{text}\n</earlier_conversation>"}
        ],
    }


def _as_abandoned_branch(summary: SummaryMessage) -> LlmMessage:
    """Render what was tried on a branch that was left.

    Told plainly that it is another attempt and not the current one. A summary
    handed over without that framing reads as work already done here, and the
    model reports a file as moved that is still sitting where it was.
    """
    text = summary.text
    if summary.files:
        listed = "\n".join(f"- {path}" for path in summary.files[-SUMMARY_FILES:])
        text = f"{text}\n\nFiles it touched:\n{listed}"

    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "<abandoned_branch>\n"
                    "This is what you tried on a different branch of this "
                    "conversation. That branch was set aside, so any change "
                    "described below MAY NO LONGER BE IN PLACE - check before "
                    "relying on one.\n\n"
                    f"{text}\n</abandoned_branch>"
                ),
            }
        ],
    }


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
    for cls in (
        UserMessage,
        AssistantMessage,
        ToolResultMessage,
        SummaryMessage,
        ContextEdit,
        NoteMessage,
        MetaMessage,
    )
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
