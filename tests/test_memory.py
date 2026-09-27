"""Tests for remembering and forgetting."""

from pathlib import Path

import pytest

from aven.harness import memory
from aven.harness.context import find, read
from aven.toolkit import memory_tools


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "AVEN.md").write_text("# 我的规矩\n\n- 邮件写短一点\n")
    return tmp_path


@pytest.fixture
def tools(box):
    return {t.name: t for t in memory_tools(box)}


# --- the file format ---------------------------------------------------------


def test_what_the_person_wrote_is_never_touched(box, tools):
    tools["remember"](fact="财务邮箱是 finance@corp.com")
    tools["remember"](fact="发票归到 报销/")

    text = (box / "AVEN.md").read_text()
    head, facts = memory.split(text)

    assert head.strip() == "# 我的规矩\n\n- 邮件写短一点", "their half is byte for byte"
    assert facts == ["财务邮箱是 finance@corp.com", "发票归到 报销/"]
    assert text.index("邮件写短一点") < text.index(memory.MARKER), "and stays on top"


def test_a_file_that_does_not_exist_yet_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    fresh = tmp_path / "新目录"
    fresh.mkdir()
    tools = {t.name: t for t in memory_tools(fresh)}

    tools["remember"](fact="这里放合同")

    assert memory.recall(fresh / "AVEN.md") == ["这里放合同"]


def test_remembering_the_same_thing_twice_changes_nothing(box, tools):
    tools["remember"](fact="我老板是 Sam")
    before = (box / "AVEN.md").read_text()

    result = tools["remember"](fact="我老板是 Sam")

    assert "already remembered" in result.output
    assert (box / "AVEN.md").read_text() == before
    assert result.undo is None, "nothing happened, so there is nothing to undo"


def test_there_is_a_ceiling(box):
    path = box / "AVEN.md"
    for n in range(memory.LIMIT):
        memory.add(path, f"第 {n} 条")

    with pytest.raises(memory.Full, match="forget one first"):
        memory.add(path, "再来一条")


# --- forgetting --------------------------------------------------------------


def test_forgetting_matches_loosely(box, tools):
    """The model is recalling a line it saw in a prompt, not copying it."""
    tools["remember"](fact="我老板是 Sam")

    result = tools["forget"](fact="老板是sam")

    assert "forgot: 我老板是 Sam" in result.output
    assert memory.recall(box / "AVEN.md") == []


def test_forgetting_something_never_remembered_says_so(box, tools):
    result = tools["forget"](fact="从来没记过的事")

    assert "nothing remembered" in result.output
    assert result.undo is None


def test_forgetting_leaves_the_others_alone(box, tools):
    for fact in ("一", "二", "三"):
        tools["remember"](fact=fact)

    tools["forget"](fact="二")

    assert memory.recall(box / "AVEN.md") == ["一", "三"]


# --- undo --------------------------------------------------------------------


def test_remembering_can_be_undone(box, tools):
    result = tools["remember"](fact="记错了的事")
    assert memory.recall(box / "AVEN.md") == ["记错了的事"]

    result.undo()

    assert memory.recall(box / "AVEN.md") == []
    assert "邮件写短一点" in (box / "AVEN.md").read_text()


def test_forgetting_can_be_undone(box, tools):
    tools["remember"](fact="其实还要用的事")

    result = tools["forget"](fact="其实还要用的事")
    result.undo()

    assert memory.recall(box / "AVEN.md") == ["其实还要用的事"]


def test_both_are_reversible_so_neither_needs_approval(tools):
    assert tools["remember"].risk == "reversible"
    assert tools["forget"].risk == "reversible"


# --- where it goes -----------------------------------------------------------


def test_global_facts_go_to_the_home_file(box, tools):
    tools["remember"](fact="我老板是 Sam", scope="global")

    assert memory.recall(Path.home() / ".aven" / "AVEN.md") == ["我老板是 Sam"]
    assert memory.recall(box / "AVEN.md") == [], "and not into this folder"


# --- the loop back -----------------------------------------------------------


def test_what_is_remembered_is_read_back_as_an_instruction(box, tools):
    """The whole point: next session it arrives in the system prompt."""
    tools["remember"](fact="财务邮箱是 finance@corp.com")

    rendered = read(find(box))

    assert "财务邮箱是 finance@corp.com" in rendered
    assert "邮件写短一点" in rendered, "alongside what they wrote themselves"
