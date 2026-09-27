"""Tests for bringing tools in when they are needed."""

from dataclasses import replace

from aven.harness.agent import run
from aven.harness.messages import AssistantMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.harness.toolbox import ToolBox, resolve
from aven.harness.tools import tool


@tool()
def read_thing() -> str:
    """Always here."""
    return "read"


@tool()
def check_calendar() -> str:
    """Only when asked for."""
    return "empty"


def box() -> ToolBox:
    return ToolBox(core=[read_thing], groups={"calendar": [check_calendar]})


# --- the box -----------------------------------------------------------------


def test_only_the_core_and_the_opener_start_in_play():
    assert [t.name for t in box().active()] == ["read_thing", "use_tools"]


def test_bringing_a_group_in_makes_its_tools_callable():
    b = box()
    b.bring_in("calendar")

    assert "check_calendar" in [t.name for t in b.active()]


def test_the_opener_disappears_once_nothing_is_left_to_bring_in():
    """A tool that can only fail is a turn the model can waste."""
    b = box()
    b.bring_in("calendar")

    assert "use_tools" not in [t.name for t in b.active()]


def test_the_catalogue_names_what_is_waiting_and_says_when_to_want_it():
    listed = box().catalogue({"calendar": "看日程。用户提到会议时拿这组。"})

    assert "calendar" in listed and "用户提到会议时" in listed


def test_an_empty_group_is_not_offered():
    """On Linux there are no macOS tools; offering the group would only fail."""
    b = ToolBox(core=[read_thing], groups={"calendar": []})

    assert b.dormant() == []
    assert [t.name for t in b.active()] == ["read_thing"]


# --- the opener as the model sees it -----------------------------------------


def test_asking_for_a_group_twice_says_so_rather_than_pretending():
    b = box()
    opener = b.active()[-1]
    opener(group="calendar")

    assert "already loaded" in opener(group="calendar").output


def test_asking_for_a_group_that_is_not_there_lists_what_is():
    opener = box().active()[-1]

    output = opener(group="没有这个").output

    assert "no group called" in output and "calendar" in output


def test_loading_is_read_only_so_it_never_waits_for_approval():
    assert box().active()[-1].risk == "read"


# --- inside the loop ---------------------------------------------------------


def test_resolve_takes_a_list_or_something_that_decides_later():
    assert [t.name for t in resolve([read_thing])] == ["read_thing"]
    assert [t.name for t in resolve(box().active)] == ["read_thing", "use_tools"]
    assert resolve(None) == []


async def test_a_tool_brought_in_this_turn_is_callable_the_next(tmp_path):
    """The list is resolved per turn, or the group arrives too late to use."""
    session = Session.open(tmp_path / "s.jsonl")
    b = box()
    turns = [
        AssistantMessage(
            tool_calls=[ToolCall(name="use_tools", args={"group": "calendar"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(
            tool_calls=[ToolCall(name="check_calendar", args={})], stop_reason="tool_use"
        ),
        AssistantMessage(text="空的"),
    ]

    def model(_llm_messages):
        return replace(turns.pop(0), id=new_id())

    async for _event in run(session=session, prompt="看看日程", model=model, tools=b.active):
        pass

    results = [m.output for m in session.history() if m.kind == "tool_result"]
    assert "loaded calendar" in results[0]
    assert results[1] == "empty", "and it really ran, rather than reporting no such tool"


async def test_a_dormant_tool_cannot_be_called_before_it_is_brought_in(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    turns = [
        AssistantMessage(
            tool_calls=[ToolCall(name="check_calendar", args={})], stop_reason="tool_use"
        ),
        AssistantMessage(text="哦"),
    ]

    def model(_llm_messages):
        return replace(turns.pop(0), id=new_id())

    async for _event in run(session=session, prompt="看看日程", model=model, tools=box().active):
        pass

    assert "no such tool" in [m.output for m in session.history() if m.kind == "tool_result"][0]
