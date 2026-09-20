"""Tests for the stored-message / LLM-message split."""

from aven.core.messages import (
    AssistantMessage,
    NoteMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)


def test_ids_are_unique():
    assert UserMessage(text="a").id != UserMessage(text="a").id


def test_notes_never_reach_the_model():
    stored = [UserMessage(text="hi"), NoteMessage(text="internal")]
    assert len(to_llm(stored)) == 1


def test_parallel_tool_results_merge_into_one_message():
    a = ToolCall(name="find_files", args={})
    b = ToolCall(name="read_calendar", args={})
    stored = [
        UserMessage(text="go"),
        AssistantMessage(tool_calls=[a, b], stop_reason="tool_use"),
        ToolResultMessage(tool_call_id=a.id, tool_name="find_files", output="3"),
        ToolResultMessage(tool_call_id=b.id, tool_name="read_calendar", output="0"),
    ]
    seen = to_llm(stored)

    assert len(seen) == 3, "the two tool results must not become two messages"
    assert [b["type"] for b in seen[-1]["content"]] == ["tool_result", "tool_result"]


def test_tool_result_does_not_merge_into_a_human_message():
    """A user's own text is also role=user. Results must not be appended to it."""
    call = ToolCall(name="x", args={})
    stored = [
        UserMessage(text="hi"),
        ToolResultMessage(tool_call_id=call.id, tool_name="x", output="ok"),
    ]
    seen = to_llm(stored)

    assert len(seen) == 2
    assert seen[0]["content"][0]["type"] == "text"
    assert seen[1]["content"][0]["type"] == "tool_result"


def test_assistant_with_no_text_emits_no_empty_text_block():
    call = ToolCall(name="x", args={})
    seen = to_llm([AssistantMessage(text="", tool_calls=[call])])
    assert [b["type"] for b in seen[0]["content"]] == ["tool_use"]


def test_failed_tool_result_keeps_is_error():
    seen = to_llm([ToolResultMessage(tool_call_id="1", tool_name="x", output="boom", is_error=True)])
    assert seen[0]["content"][0]["is_error"] is True
