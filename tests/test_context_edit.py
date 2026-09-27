"""Tests for append-only edits of what the model sees."""

from aven.harness.messages import (
    AssistantMessage,
    ContextEdit,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)
from aven.harness.session import Session


def said(messages) -> str:
    return "\n".join(
        block.get("text", "") or str(block.get("content", ""))
        for message in to_llm(messages)
        for block in message["content"]
    )


def well_formed(messages) -> bool:
    asked, answered = set(), set()
    for message in to_llm(messages):
        for block in message["content"]:
            if block["type"] == "tool_use":
                asked.add(block["id"])
            elif block["type"] == "tool_result":
                answered.add(block["tool_use_id"])
    return asked <= answered


def test_an_edit_drops_its_target_from_the_projection(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    first = session.append(UserMessage(text="很久以前说的话"))
    session.append(UserMessage(text="刚才说的话"))
    session.append(ContextEdit(target=first.id))

    assert "很久以前说的话" not in said(session.history())
    assert "刚才说的话" in said(session.history())


def test_the_target_is_untouched_on_disk_and_on_the_path(tmp_path):
    """The point of doing this by appending: nothing is edited or deleted."""
    path = tmp_path / "s.jsonl"
    session = Session.open(path)
    first = session.append(UserMessage(text="要被藏起来的"))
    session.append(ContextEdit(target=first.id))

    assert "要被藏起来的" in path.read_text(encoding="utf-8")
    assert first.id in {m.id for m in session.history()}


def test_a_replacement_stands_in_for_the_content(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    secret = session.append(UserMessage(text="我的密码是 hunter2"))
    session.append(ContextEdit(target=secret.id, replacement="[已脱敏]"))

    projected = said(session.history())
    assert "hunter2" not in projected
    assert "[已脱敏]" in projected


def test_a_later_edit_wins_and_can_take_back_an_earlier_one(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    target = session.append(UserMessage(text="原文"))
    session.append(ContextEdit(target=target.id))
    session.append(ContextEdit(target=target.id, replacement="改过的"))

    assert "改过的" in said(session.history())


def test_an_edit_is_branch_relative(tmp_path):
    """Check out a point before the edit and the original is back - which falls
    out of the tree rather than being arranged for."""
    session = Session.open(tmp_path / "s.jsonl")
    target = session.append(UserMessage(text="原文"))
    before_edit = session.append(AssistantMessage(text="收到"))
    session.append(ContextEdit(target=target.id))

    assert "原文" not in said(session.history())

    session.checkout(before_edit.id)
    assert "原文" in said(session.history())


def test_a_rewritten_result_stays_a_result(tmp_path):
    """The call it answers is still in the transcript and still needs an answer."""
    session = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="read_file", args={"path": "big.txt"})
    session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
    result = session.append(
        ToolResultMessage(tool_call_id=call.id, tool_name="read_file", output="x" * 5000)
    )
    session.append(ContextEdit(target=result.id, replacement="[5000 字符,已省略]"))

    projected = to_llm(session.history())
    blocks = [b for m in projected for b in m["content"]]

    assert any(b["type"] == "tool_result" and "已省略" in b["content"] for b in blocks)
    assert well_formed(session.history())


def test_dropping_an_assistant_turn_takes_its_results_with_it(tmp_path):
    """Otherwise the projection carries a tool_result answering nothing."""
    session = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={})
    reply = session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
    session.append(ToolResultMessage(tool_call_id=call.id, tool_name="echo", output="结果"))
    session.append(ContextEdit(target=reply.id))

    assert "结果" not in said(session.history())
    assert well_formed(session.history())


def test_rewriting_an_assistant_turn_also_takes_its_results(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={})
    reply = session.append(AssistantMessage(text="我调个工具", tool_calls=[call],
                                            stop_reason="tool_use"))
    session.append(ToolResultMessage(tool_call_id=call.id, tool_name="echo", output="结果"))
    session.append(ContextEdit(target=reply.id, replacement="我做了点事"))

    projected = said(session.history())
    assert "我做了点事" in projected
    assert "结果" not in projected
    assert well_formed(session.history())


def test_an_edit_survives_a_round_trip_through_the_file(tmp_path):
    path = tmp_path / "s.jsonl"
    session = Session.open(path)
    target = session.append(UserMessage(text="原文"))
    session.append(ContextEdit(target=target.id, replacement="换过的"))

    assert "换过的" in said(Session.open(path).history())
