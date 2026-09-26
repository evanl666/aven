"""Tests for reading the session tree, and for copying a branch out of it."""

import pytest

from aven.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from aven.core.session import Session
from aven.core.tree import interesting, render, walk


def conversation(session, *pairs):
    """Append (question, answer) pairs, returning the messages appended."""
    out = []
    for question, answer in pairs:
        out.append(session.append(UserMessage(text=question)))
        out.append(session.append(AssistantMessage(text=answer, stop_reason="end_turn")))
    return out


# --- what the view leaves out ------------------------------------------------


def test_tool_results_are_not_places_to_go_back_to():
    """Nobody returns to the middle of a directory listing."""
    assert not interesting(
        ToolResultMessage(tool_call_id="c1", tool_name="list_dir", output="a.pdf\nb.pdf")
    )
    assert interesting(UserMessage(text="列一下"))
    assert interesting(AssistantMessage(text="有两个文件", stop_reason="end_turn"))


def test_a_reply_that_only_called_tools_is_not_a_place_either():
    """There is nothing on its line to recognise it by."""
    assert not interesting(AssistantMessage(text="", stop_reason="tool_use"))


# --- the shape ---------------------------------------------------------------


def test_a_conversation_that_never_branched_comes_back_flat(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, ("问题一", "回答一"), ("问题二", "回答二"))

    assert [node.depth for node in walk(session)] == [0, 0, 0, 0]
    assert all(node.on_path for node in walk(session))


def test_a_branch_shows_both_attempts(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    first, _ = conversation(session, ("整理下载目录", "整理好了"))

    # Go back and ask differently. The first attempt is still in the file.
    session.checkout(first.id)
    conversation(session, ("换个说法:按月份分类", "按月份分好了"))

    texts = [getattr(node.message, "text", "") for node in walk(session)]
    assert "整理好了" in texts
    assert "按月份分好了" in texts

    on_path = {t for node in walk(session) if node.on_path
               for t in [getattr(node.message, "text", "")]}
    assert "按月份分好了" in on_path
    assert "整理好了" not in on_path, "the abandoned branch is not on the path"


def test_only_a_real_fork_earns_indentation(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    first, _ = conversation(session, ("问题", "回答一"))
    session.checkout(first.id)
    conversation(session, ("", "回答二"))

    depths = {getattr(n.message, "text", ""): n.depth for n in walk(session)}
    assert depths["问题"] == 0, "before the fork"
    assert depths["回答一"] == 1 and depths["回答二"] == 1, "the two sides of it"


def test_the_head_is_marked_and_nothing_else_is(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, ("问题", "回答"))

    assert render(session).count("▸") == 1
    assert session.head[:6] in render(session)


def test_an_empty_session_says_so_rather_than_rendering_nothing(tmp_path):
    assert "空" in render(Session.open(tmp_path / "s.jsonl"))


def test_a_long_message_is_cut_so_the_shape_stays_visible(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="很长" * 200))

    assert all(len(line) < 120 for line in render(session).splitlines())


# --- short ids ---------------------------------------------------------------


def test_a_prefix_is_enough_to_name_a_message(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    message = session.append(UserMessage(text="问题"))

    assert session.find(message.id[:6]) == message.id
    assert session.find(message.id) == message.id


def test_an_ambiguous_prefix_is_refused_rather_than_guessed(tmp_path):
    """Guessing would check out the wrong branch, silently."""
    session = Session.open(tmp_path / "s.jsonl")
    a = session.append(UserMessage(text="一"))
    b = session.append(UserMessage(text="二"))
    # Force a collision the way only a test can: real ids never share a prefix
    # this long by accident, which is why six characters is enough in practice.
    session._messages["dead0000aaaa"] = a
    session._messages["dead0000bbbb"] = b

    with pytest.raises(KeyError, match="matches 2"):
        session.find("dead0000")


def test_an_unknown_prefix_is_an_error(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="问题"))

    with pytest.raises(KeyError, match="no message"):
        session.find("zzzzzz")


# --- forking -----------------------------------------------------------------


def test_a_fork_copies_the_branch_and_leaves_the_original_alone(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, ("问题一", "回答一"), ("问题二", "回答二"))
    before = (tmp_path / "s.jsonl").read_text()

    forked = session.fork(tmp_path / "fork.jsonl")

    assert len(forked) == len(session)
    assert forked.head == session.head
    assert (tmp_path / "s.jsonl").read_text() == before, "not one byte"


def test_a_fork_can_stop_at_an_earlier_message(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    messages = conversation(session, ("问题一", "回答一"), ("问题二", "回答二"))

    forked = session.fork(tmp_path / "fork.jsonl", at=messages[1].id[:6])

    assert [getattr(m, "text", "") for m in forked.history()] == ["问题一", "回答一"]
    assert forked.head == messages[1].id


def test_a_fork_keeps_the_ids_so_a_note_still_finds_the_message(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    messages = conversation(session, ("问题", "回答"))

    forked = session.fork(tmp_path / "fork.jsonl")

    assert forked.find(messages[0].id) == messages[0].id


def test_a_fork_can_be_talked_to_without_disturbing_the_original(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, ("问题", "回答"))
    forked = session.fork(tmp_path / "fork.jsonl")

    forked.append(UserMessage(text="在新会话里接着问"))

    assert len(forked) == len(session) + 1
    assert [getattr(m, "text", "") for m in Session.open(tmp_path / "s.jsonl").history()] == [
        "问题",
        "回答",
    ]


def test_a_fork_refuses_to_land_on_an_existing_file(tmp_path):
    """Overwriting a session is the one thing the append-only file rules out."""
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, ("问题", "回答"))
    (tmp_path / "taken.jsonl").write_text("")

    with pytest.raises(FileExistsError):
        session.fork(tmp_path / "taken.jsonl")
