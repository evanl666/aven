"""Tests for the staging tray."""

from dataclasses import replace

import pytest

from aven.core.agent import run
from aven.core.events import ToolEnd
from aven.core.messages import AssistantMessage, ToolCall, new_id
from aven.core.session import Session
from aven.core.tools import ToolResult, tool
from aven.tx import Tray


def recorder():
    """A pair of reversible/irreversible tools over one observable list."""
    log: list[str] = []

    @tool(risk="read")
    def look() -> str:
        return "looked"

    @tool(risk="reversible", preview="add {item}")
    def add(item: str) -> ToolResult:
        log.append(item)
        return ToolResult(output=f"added {item}", undo=lambda: log.remove(item))

    @tool(risk="irreversible", preview="send {item}")
    def send(item: str) -> str:
        log.append(f"sent:{item}")
        return f"sent {item}"

    return log, look, add, send


def test_read_tools_never_enter_the_tray():
    _, look, _, _ = recorder()
    tray = Tray()
    output, staged = tray.execute(look, {})

    assert output == "looked"
    assert staged is False
    assert len(tray) == 0, "a read changed nothing, so there is nothing to review"


def test_reversible_runs_immediately_and_is_recorded():
    log, _, add, _ = recorder()
    tray = Tray()
    output, staged = tray.execute(add, {"item": "a"})

    assert log == ["a"], "it really ran"
    assert staged is False
    assert output == "added a"
    assert [e.state for e in tray.entries] == ["applied"]


def test_irreversible_does_not_run():
    log, _, _, send = recorder()
    tray = Tray()
    output, staged = tray.execute(send, {"item": "a"})

    assert log == [], "nothing was sent"
    assert staged is True
    assert "waiting for the user to approve" in output, "the model is told honestly"
    assert [e.state for e in tray.entries] == ["pending"]


def test_commit_fires_the_pending_calls():
    log, _, _, send = recorder()
    tray = Tray()
    tray.execute(send, {"item": "a"})

    committed = tray.commit()

    assert log == ["sent:a"]
    assert [e.state for e in committed] == ["committed"]
    assert tray.pending() == []


def test_discard_drops_them_without_firing():
    log, _, _, send = recorder()
    tray = Tray()
    tray.execute(send, {"item": "a"})

    tray.discard()

    assert log == []
    assert [e.state for e in tray.entries] == ["discarded"]


def test_undo_runs_newest_first():
    order: list[str] = []

    @tool(risk="reversible")
    def step(n: str) -> ToolResult:
        return ToolResult(output=n, undo=lambda: order.append(n))

    tray = Tray()
    for n in ("a", "b", "c"):
        tray.execute(step, {"n": n})

    tray.undo()

    assert order == ["c", "b", "a"], "a later change may depend on an earlier one"
    assert [e.state for e in tray.entries] == ["undone"] * 3


def test_a_committed_irreversible_action_stays_done():
    log, _, add, send = recorder()
    tray = Tray()
    tray.execute(add, {"item": "a"})
    tray.execute(send, {"item": "b"})
    tray.commit()

    rolled = tray.undo()

    assert [e.tool for e in rolled] == ["add"], "only the reversible one came back"
    assert "sent:b" in log, "irreversible means irreversible, even after commit"


def test_commit_stops_at_the_first_failure():
    fired: list[str] = []

    @tool(risk="irreversible")
    def maybe(item: str) -> str:
        if item == "bad":
            raise RuntimeError("smtp down")
        fired.append(item)
        return item

    tray = Tray()
    for item in ("ok", "bad", "later"):
        tray.execute(maybe, {"item": item})

    tray.commit()

    assert fired == ["ok"]
    assert [e.state for e in tray.entries] == ["committed", "failed", "pending"]


def test_a_run_without_a_tray_still_fires_nothing_irreversible(tmp_path):
    """Safe by omission: forgetting the tray must not mean sending the mail."""
    log, _, _, send = recorder()
    s = Session.open(tmp_path / "s.jsonl")
    script = [
        AssistantMessage(
            tool_calls=[ToolCall(name="send", args={"item": "a"})], stop_reason="tool_use"
        ),
        AssistantMessage(text="staged"),
    ]

    events = list(
        run(
            session=s,
            prompt="go",
            model=lambda _: replace(script.pop(0), id=new_id()),
            tools=[send],
        )
    )

    assert log == []
    assert next(e for e in events if isinstance(e, ToolEnd)).staged is True


def test_rewind_point_names_the_message_the_changes_came_from(tmp_path):
    log, _, add, _ = recorder()
    s = Session.open(tmp_path / "s.jsonl")
    tray = Tray()
    script = [
        AssistantMessage(
            tool_calls=[ToolCall(name="add", args={"item": "a"})], stop_reason="tool_use"
        ),
        AssistantMessage(text="done"),
    ]

    list(
        run(
            session=s,
            prompt="go",
            model=lambda _: replace(script.pop(0), id=new_id()),
            tools=[add],
            tray=tray,
        )
    )

    assistant_ids = [m.id for m in s.history() if m.kind == "assistant"]
    assert tray.rewind_point() == assistant_ids[0]
