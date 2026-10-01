"""Tests for the staging tray."""

from dataclasses import replace

import pytest

from aven.harness.agent import run
from aven.harness.events import ToolEnd
from aven.harness.messages import AssistantMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.harness.tools import ToolResult, tool
from aven.harness.tx import Tray


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


def test_a_failing_undo_stops_the_batch_and_is_recorded_not_raised():
    """A raise here would leave the caller holding an exception and no way to
    tell how much of the rollback had already happened - and the surface showing
    it would have nothing to draw, so the click looks like it did nothing."""

    @tool(risk="reversible")
    def step(n: str) -> ToolResult:
        def back() -> None:
            if n == "b":
                raise OSError("the file is gone")

        return ToolResult(output=n, undo=back)

    tray = Tray()
    for n in ("a", "b"):
        tray.execute(step, {"n": n})

    rolled = tray.undo()

    assert rolled == [], "b is newest, so it fails before a is reached"
    states = {e.args["n"]: e.state for e in tray.entries}
    assert states["b"] == "failed"
    assert "the file is gone" in [e for e in tray.entries if e.state == "failed"][0].output
    assert states["a"] == "applied", "still undoable, so it can be tried again"


def test_undo_rewinds_the_conversation_only_as_far_as_it_rolled_back():
    """The tray outlives the turn that filled it, so it can still hold a
    discarded entry from several turns ago. Rewinding to that one would throw
    away turns whose work was never undone."""
    log, _, add, send = recorder()
    tray = Tray()

    tray.execute(send, {"item": "kettle"}, origin_message_id="turn-1")
    tray.discard()  # never happened, but the entry stays in the tray
    tray.execute(add, {"item": "notes"}, origin_message_id="turn-2")

    rolled = tray.undo()

    assert [e.origin_message_id for e in rolled] == ["turn-2"]
    assert tray.rewind_point(rolled) == "turn-2", (
        "turn-1 was discarded, not undone - the conversation must not go back to it"
    )


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


async def test_a_run_without_a_tray_still_fires_nothing_irreversible(tmp_path):
    """Safe by omission: forgetting the tray must not mean sending the mail."""
    log, _, _, send = recorder()
    s = Session.open(tmp_path / "s.jsonl")
    script = [
        AssistantMessage(
            tool_calls=[ToolCall(name="send", args={"item": "a"})], stop_reason="tool_use"
        ),
        AssistantMessage(text="staged"),
    ]

    events = [
        event
        async for event in run(
            session=s,
            prompt="go",
            model=lambda _: replace(script.pop(0), id=new_id()),
            tools=[send],
        )
    ]

    assert log == []
    assert next(e for e in events if isinstance(e, ToolEnd)).staged is True


async def test_rewind_point_names_the_message_the_changes_came_from(tmp_path):
    log, _, add, _ = recorder()
    s = Session.open(tmp_path / "s.jsonl")
    tray = Tray()
    script = [
        AssistantMessage(
            tool_calls=[ToolCall(name="add", args={"item": "a"})], stop_reason="tool_use"
        ),
        AssistantMessage(text="done"),
    ]

    async for _event in run(
        session=s,
        prompt="go",
        model=lambda _: replace(script.pop(0), id=new_id()),
        tools=[add],
        tray=tray,
    ):
        pass

    assistant_ids = [m.id for m in s.history() if m.kind == "assistant"]
    assert tray.rewind_point() == assistant_ids[0]


# --- unattended ---------------------------------------------------------------
#
# The flag that says nobody is here. What it must change is the TIMING: the
# call happens while the model can still read the result of it. What it must
# not change is the RECORD: an irreversible call that nobody approved has to
# say so on the entry.


def test_unattended_runs_irreversible_calls_at_once():
    log, _, _, send = recorder()
    tray = Tray(unattended=True)
    output, staged = tray.execute(send, {"item": "a"})

    assert staged is False, "staged work in a run with nobody to unstage it"
    assert output == "sent a", "the model must see what the call returned"
    assert log == ["sent:a"]
    assert tray.pending() == []


def test_unattended_still_records_that_nobody_approved_it():
    _, _, _, send = recorder()
    tray = Tray(unattended=True)
    tray.execute(send, {"item": "a"})

    entry = tray.entries[-1]
    assert entry.state == "committed"
    assert entry.approved_by, "an unaudited irreversible call"
    assert "unattended" in entry.approved_by


def test_unattended_is_off_by_default():
    """Omission is the safe direction, and the default is omission."""
    log, _, _, send = recorder()
    tray = Tray()
    _, staged = tray.execute(send, {"item": "a"})

    assert staged is True
    assert log == []


def test_unattended_fires_the_same_calls_committing_later_would_have():
    """The claim the change rests on: same set, different moment.

    If these two ever diverge, --yes means something other than what its help
    text says, and the divergence is the part nobody would notice.
    """
    one, _, _, send_one = recorder()
    staged_then_committed = Tray()
    staged_then_committed.execute(send_one, {"item": "a"})
    staged_then_committed.execute(send_one, {"item": "b"})
    staged_then_committed.commit()

    two, _, _, send_two = recorder()
    straight_through = Tray(unattended=True)
    straight_through.execute(send_two, {"item": "a"})
    straight_through.execute(send_two, {"item": "b"})

    assert one == two == ["sent:a", "sent:b"]
    assert [e.state for e in staged_then_committed.entries] == [
        e.state for e in straight_through.entries
    ]
