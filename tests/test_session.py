"""Tests for the append-only message tree."""

import json

import pytest

from aven.core.messages import AssistantMessage, NoteMessage, UserMessage
from aven.core.session import Session


def test_opening_a_missing_file_is_not_an_error(tmp_path):
    s = Session.open(tmp_path / "new" / "s.jsonl")
    assert len(s) == 0
    assert s.head is None


def test_append_sets_parent_to_head(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    a = s.append(UserMessage(text="a"))
    b = s.append(AssistantMessage(text="b"))

    assert a.parent_id is None
    assert b.parent_id == a.id
    assert s.head == b.id


def test_history_is_root_to_head(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    s.append(UserMessage(text="1"))
    s.append(AssistantMessage(text="2"))
    s.append(UserMessage(text="3"))

    assert [m.text for m in s.history()] == ["1", "2", "3"]


def test_branching_keeps_both_paths_in_one_file(tmp_path):
    path = tmp_path / "s.jsonl"
    s = Session.open(path)

    ask = s.append(UserMessage(text="question"))
    a = s.append(AssistantMessage(text="answer A"))

    s.checkout(ask.id)
    b = s.append(AssistantMessage(text="answer B"))

    assert [m.text for m in s.history()] == ["question", "answer B"]
    s.checkout(a.id)
    assert [m.text for m in s.history()] == ["question", "answer A"]

    assert len(s) == 3
    assert len(path.read_text().splitlines()) == 3, "branching must not rewrite lines"
    assert {m.id for m in s.leaves()} == {a.id, b.id}
    assert len(s.children(ask.id)) == 2


def test_reopening_restores_the_tree(tmp_path):
    path = tmp_path / "s.jsonl"
    s = Session.open(path)
    ask = s.append(UserMessage(text="整理发票"))
    s.append(AssistantMessage(text="好"))
    s.checkout(ask.id)
    last = s.append(NoteMessage(text="branch"))

    again = Session.open(path)

    assert len(again) == 3
    assert again.head == last.id, "head is the last line appended"
    assert [m.text for m in again.history()] == ["整理发票", "branch"]


def test_file_is_one_json_object_per_line(tmp_path):
    path = tmp_path / "s.jsonl"
    s = Session.open(path)
    s.append(UserMessage(text="整理发票"))
    s.append(AssistantMessage(text="好"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["kind"] for line in lines)
    assert "整理发票" in lines[0], "stored unescaped so a human can read the file"


def test_checkout_rejects_unknown_ids(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    with pytest.raises(KeyError):
        s.checkout("nope")


def test_appending_the_same_message_twice_is_rejected(tmp_path):
    """append mutates parent_id, so a re-append would make a node its own parent."""
    s = Session.open(tmp_path / "s.jsonl")
    m = s.append(UserMessage(text="hi"))
    s.append(AssistantMessage(text="yo"))

    with pytest.raises(ValueError, match="already in this session"):
        s.append(m)
