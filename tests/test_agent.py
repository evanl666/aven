"""Tests for the agent loop."""

from dataclasses import replace

from aven.core.agent import run
from aven.core.events import AgentEnd, AgentStart, MessageEnd, ToolEnd, ToolStart, TurnEnd, TurnStart
from aven.core.messages import AssistantMessage, ToolCall, new_id
from aven.core.session import Session


def scripted(*replies):
    """A model that returns canned replies, then repeats the last one.

    Each reply is a fresh copy: a real model never hands back a message that is
    already in the session, and Session.append now refuses one that is.
    """
    queue = list(replies)

    def model(llm_messages):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return replace(reply, id=new_id())

    return model


def test_a_plain_answer_is_one_turn(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    events = list(run(session=s, prompt="hi", model=scripted(AssistantMessage(text="hello"))))

    assert [type(e).__name__ for e in events] == [
        "AgentStart", "MessageEnd", "TurnStart", "MessageEnd", "TurnEnd", "AgentEnd",
    ]
    assert events[-1].reason == "end_turn"
    assert [m.text for m in s.history()] == ["hi", "hello"]


def test_tool_call_feeds_back_into_a_second_turn(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={"x": "ok"})
    model = scripted(
        AssistantMessage(text="working", tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    )

    events = list(run(session=s, prompt="go", model=model, tools={"echo": lambda x: x}))

    assert sum(isinstance(e, TurnStart) for e in events) == 2
    tool_end = next(e for e in events if isinstance(e, ToolEnd))
    assert tool_end.result.output == "ok"
    assert tool_end.result.is_error is False
    assert [m.kind for m in s.history()] == ["user", "assistant", "tool_result", "assistant"]


def test_a_crashing_tool_becomes_an_errored_result(tmp_path):
    """A tool_use with no tool_result is a malformed conversation."""
    s = Session.open(tmp_path / "s.jsonl")

    def boom():
        raise RuntimeError("nope")

    call = ToolCall(name="boom", args={})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="recovered"),
    )

    events = list(run(session=s, prompt="go", model=model, tools={"boom": boom}))
    result = next(e for e in events if isinstance(e, ToolEnd)).result

    assert result.is_error is True
    assert "RuntimeError: nope" in result.output
    assert events[-1].reason == "end_turn", "the loop keeps going after a tool fails"


def test_unknown_tool_is_reported_to_the_model_not_raised(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="ghost", args={})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="ok"),
    )

    result = next(
        e for e in run(session=s, prompt="go", model=model) if isinstance(e, ToolEnd)
    ).result
    assert result.is_error is True
    assert "no such tool" in result.output


def test_max_turns_stops_a_runaway_model(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    model = scripted(
        AssistantMessage(tool_calls=[ToolCall(name="echo", args={})], stop_reason="tool_use")
    )

    events = list(
        run(session=s, prompt="go", model=model, tools={"echo": lambda: "x"}, max_turns=3)
    )

    assert sum(isinstance(e, TurnStart) for e in events) == 3
    assert events[-1].reason == "max_turns"


def test_every_appended_message_emits_exactly_one_message_end(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={"x": "1"})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    )

    events = list(run(session=s, prompt="go", model=model, tools={"echo": lambda x: x}))
    ended = [e.message.id for e in events if isinstance(e, MessageEnd)]

    assert ended == [m.id for m in s.history()]


def test_the_model_sees_the_current_path_growing(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    seen = []
    call = ToolCall(name="echo", args={"x": "1"})
    replies = [
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    ]

    def model(llm_messages):
        seen.append(len(llm_messages))
        return replies.pop(0)

    list(run(session=s, prompt="go", model=model, tools={"echo": lambda x: x}))
    assert seen == [1, 3], "turn 2 sees the user turn, the tool call, and its result"
