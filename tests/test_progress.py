"""Stall detection, tested against the run that motivated it and against the
run it must not break.

The motivating data: a benchmark task made eighty calls, eighteen distinct,
wrote one file thirty-one times and ran one compile command ten times, and
ended at the turn cap no closer than it was at call twenty.

The run it must not break: a model working through a file, where writing the
same path over and over with different content each time is what progress
looks like.
"""

import pytest

from aven.harness.agent import run
from aven.harness.events import AgentEnd, MessageEnd
from aven.harness.messages import AssistantMessage, ToolCall, UserMessage, new_id
from aven.harness.progress import REPEATS, WINDOW, Watch
from aven.harness.session import Session
from aven.harness.tools import tool


# --- the judgement itself ----------------------------------------------------


def test_the_same_call_with_the_same_answer_is_a_stall():
    watch = Watch()
    for _ in range(REPEATS):
        watch.saw("run_command", {"command": "rustc main.rs"}, "error: expected `;`")

    stuck = watch.stuck()
    assert stuck is not None
    what, times = stuck
    assert "rustc main.rs" in what
    assert times == REPEATS


def test_one_repeat_short_of_the_threshold_is_not():
    watch = Watch()
    for _ in range(REPEATS - 1):
        watch.saw("run_command", {"command": "pytest -q"}, "1 failed")

    assert watch.stuck() is None


def test_the_same_call_with_a_different_answer_is_progress():
    """A command re-run after a real edit says something different. That is
    the whole distinction, and the reason the result is part of the key."""
    watch = Watch()
    for n in range(WINDOW):
        watch.saw("run_command", {"command": "pytest -q"}, f"{WINDOW - n} failed")

    assert watch.stuck() is None


def test_writing_one_file_many_times_is_not_a_stall():
    """The false positive that would have fired in the real run.

    `write /app/main.c.rs` appeared thirty-one times there, and every one of
    them wrote different content - the preview is only the path. A detector
    keyed on the call alone fires on any edit loop, which is what working on a
    file looks like.
    """
    watch = Watch()
    for n in range(WINDOW):
        watch.saw("write_file", {"path": "main.c.rs", "content": f"version {n}"},
                  f"wrote {20 + n} lines")

    assert watch.stuck() is None


def test_argument_order_does_not_make_two_calls_different():
    watch = Watch()
    watch.saw("edit", {"path": "a", "old": "x"}, "done")
    watch.saw("edit", {"old": "x", "path": "a"}, "done")
    watch.saw("edit", {"path": "a", "old": "x"}, "done")

    assert watch.stuck() is not None, "the same call counted as three different ones"


def test_what_scrolled_out_of_the_window_no_longer_counts():
    """Three identical calls an hour ago are not a stall now."""
    watch = Watch(window=4, repeats=3)
    for _ in range(3):
        watch.saw("run_command", {"command": "ls"}, "a\nb")
    assert watch.stuck() is not None

    for n in range(4):
        watch.saw("run_command", {"command": f"cat f{n}"}, f"contents {n}")
    assert watch.stuck() is None


def test_being_told_wipes_the_slate():
    """Otherwise the next identical call ends the run before the model has had
    a chance to act on what it was told."""
    watch = Watch()
    for _ in range(REPEATS):
        watch.saw("run_command", {"command": "make"}, "Error 1")

    assert watch.stuck() is not None
    watch.note(*watch.stuck())

    assert watch.told() is True
    assert watch.stuck() is None


def test_the_sentence_says_what_to_do_and_what_not_to():
    watch = Watch()
    said = watch.note("run_command [('command', 'make')]", 4)

    assert "4 times" in said
    assert "make" in said
    assert "change approach" in said.lower()
    assert "blocking you" in said
    assert "do not report the work as done" in said.lower(), (
        "a model told to stop must not conclude by claiming success"
    )


# --- the loop ----------------------------------------------------------------


def looping_model(*, calls: int):
    """A model that makes the same call forever, like the real one did."""
    made = {"n": 0}

    async def ask(_messages):
        made["n"] += 1
        if made["n"] > calls:
            return AssistantMessage(text="giving up", stop_reason="end_turn", id=new_id())
        return AssistantMessage(
            tool_calls=[ToolCall(name="run_command", args={"command": "rustc main.rs"})],
            stop_reason="tool_use",
            id=new_id(),
        )

    return ask


@tool(risk="read", preview="run: {command}")
def run_command(command: str) -> str:
    """Always the same complaint, however often it is asked."""
    return "error: expected `;`, found `}`"


async def test_the_model_is_told_once_and_then_the_turn_ends(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    reasons, nudges = [], []

    async for event in run(
        session=session,
        prompt="make it compile",
        model=looping_model(calls=40),
        tools=[run_command],
        watch=Watch(),
        max_turns=40,
    ):
        if isinstance(event, AgentEnd):
            reasons.append(event.reason)
        if isinstance(event, MessageEnd) and isinstance(event.message, UserMessage):
            if event.message.source == "progress":
                nudges.append(event.message.text)

    assert reasons == ["stalled"]
    assert len(nudges) == 1, f"told {len(nudges)} times; it must be once then stop"
    assert "rustc main.rs" in nudges[0]


async def test_it_stops_long_before_the_turn_limit(tmp_path):
    """The point. The limit would have let this run forty turns."""
    session = Session.open(tmp_path / "s.jsonl")
    turns = 0

    async for event in run(
        session=session, prompt="make it compile", model=looping_model(calls=40),
        tools=[run_command], watch=Watch(), max_turns=40,
    ):
        if isinstance(event, AgentEnd):
            break
        from aven.harness.events import TurnEnd
        if isinstance(event, TurnEnd):
            turns += 1

    assert turns < 10, f"took {turns} turns to notice"


async def test_without_a_watch_the_loop_behaves_exactly_as_before(tmp_path):
    """Omission has to be safe: a caller that passes nothing gets the old loop."""
    session = Session.open(tmp_path / "s.jsonl")
    reasons = []

    async for event in run(
        session=session, prompt="make it compile", model=looping_model(calls=6),
        tools=[run_command], max_turns=8,
    ):
        if isinstance(event, AgentEnd):
            reasons.append(event.reason)

    assert reasons == ["end_turn"], "the loop changed for callers who asked for nothing"


async def test_a_model_making_progress_is_left_alone(tmp_path):
    """Same command every turn, different answer every turn - the legitimate
    case the result-keyed check exists to protect."""
    log = {"n": 0}

    @tool(risk="read", preview="run: pytest")
    def pytest_q() -> str:
        log["n"] += 1
        return f"{10 - log['n']} failed"

    async def ask(_messages):
        if log["n"] >= 8:
            return AssistantMessage(text="all green", stop_reason="end_turn", id=new_id())
        return AssistantMessage(
            tool_calls=[ToolCall(name="pytest_q", args={})],
            stop_reason="tool_use", id=new_id(),
        )

    session = Session.open(tmp_path / "s.jsonl")
    reasons = []
    async for event in run(
        session=session, prompt="fix the tests", model=ask, tools=[pytest_q],
        watch=Watch(), max_turns=20,
    ):
        if isinstance(event, AgentEnd):
            reasons.append(event.reason)

    assert reasons == ["end_turn"], "it cut off a model that was getting somewhere"
