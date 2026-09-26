"""Tests for messages typed while a run was already in flight.

The invariant that matters most here is not about convenience. A user message
appended between a tool call and its result is a malformed conversation, which
the provider rejects outright - so where the queue is drained is the whole
design, and `test_steering_never_lands_between_a_call_and_its_result` is the
test that holds it.
"""

from dataclasses import replace

from aven.core.agent import run
from aven.core.events import AgentEnd, ToolStart
from aven.core.messages import AssistantMessage, ToolCall, new_id, to_llm
from aven.core.session import Session
from aven.core.steering import Steering
from aven.core.tools import tool


@tool()
def echo(x: str) -> str:
    """Echo a string back."""
    return x


def scripted(*replies):
    """A model that returns canned replies, then repeats the last one."""
    queue = list(replies)

    def model(_llm_messages):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return replace(reply, id=new_id())

    return model


def calling(name="echo", **args):
    return AssistantMessage(tool_calls=[ToolCall(name=name, args=args)], stop_reason="tool_use")


def done(text="好了"):
    return AssistantMessage(text=text, stop_reason="end_turn")


def said(session):
    return [(m.source, m.text) for m in session.history() if m.kind == "user"]


# --- the queue on its own ----------------------------------------------------


def test_blank_text_is_not_a_message():
    queue = Steering()
    queue.add("   ")
    queue.add("\n")

    assert queue.waiting() == 0
    assert not queue


def test_draining_takes_everything_in_the_order_it_was_typed():
    queue = Steering()
    queue.add("先别动 Downloads")
    queue.add("再看一下 报销/")

    assert queue.drain() == ["先别动 Downloads", "再看一下 报销/"]
    assert queue.drain() == [], "and leaves nothing behind"


# --- in the loop -------------------------------------------------------------


async def test_something_typed_mid_run_is_read_on_the_next_turn(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    queue = Steering()
    seen = []

    def model(llm_messages):
        seen.append(llm_messages)
        # Typed while the first turn's tool was running.
        if len(seen) == 1:
            queue.add("等等,是 报销/ 那个目录")
            return replace(calling(x="a"), id=new_id())
        return replace(done(), id=new_id())

    await _drive(run(session=session, prompt="整理一下", model=model,
                     tools=[echo], steering=queue))

    assert said(session) == [("chat", "整理一下"), ("steering", "等等,是 报销/ 那个目录")]
    asked = repr(seen[-1])
    assert "报销/" in asked, "and the model actually saw it"


async def test_steering_never_lands_between_a_call_and_its_result(tmp_path):
    """Queued the instant a tool started, which is the dangerous moment."""
    session = Session.open(tmp_path / "s.jsonl")
    queue = Steering()

    events = []
    stream = run(
        session=session,
        prompt="做两件事",
        model=scripted(
            AssistantMessage(
                tool_calls=[
                    ToolCall(name="echo", args={"x": "a"}),
                    ToolCall(name="echo", args={"x": "b"}),
                ],
                stop_reason="tool_use",
            ),
            done(),
        ),
        tools=[echo],
        steering=queue,
    )
    async for event in stream:
        events.append(event)
        if isinstance(event, ToolStart):
            queue.add("插一句")

    projected = to_llm(session.history())
    for before, after in zip(projected, projected[1:]):
        if before["role"] == "assistant" and any(
            b["type"] == "tool_use" for b in before["content"]
        ):
            assert any(
                b.get("type") == "tool_result" for b in after["content"]
            ), "a tool call is answered by the very next message, always"


async def test_a_follow_up_typed_as_the_run_ends_carries_it_on(tmp_path):
    """Queued during the last turn, when the run was about to stop."""
    session = Session.open(tmp_path / "s.jsonl")
    queue = Steering()
    replies = [0]

    def model(_llm_messages):
        replies[0] += 1
        if replies[0] == 1:
            queue.add("顺便把 报销/ 也看一下")
        return replace(done(f"第 {replies[0]} 次"), id=new_id())

    events = await _drive(
        run(session=session, prompt="整理一下", model=model, steering=queue)
    )

    assert replies[0] == 2, "the run continued instead of ending"
    assert events[-1] == AgentEnd(reason="end_turn")
    assert said(session) == [("chat", "整理一下"), ("steering", "顺便把 报销/ 也看一下")]


async def test_an_empty_queue_changes_nothing(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    events = await _drive(
        run(session=session, prompt="在吗", model=scripted(done("在")), steering=Steering())
    )

    assert events[-1] == AgentEnd(reason="end_turn")
    assert said(session) == [("chat", "在吗")]


async def test_a_loop_with_no_queue_at_all_still_runs(tmp_path):
    """steering is optional, as every collaborator of the loop is."""
    session = Session.open(tmp_path / "s.jsonl")

    events = await _drive(run(session=session, prompt="在吗", model=scripted(done("在"))))

    assert events[-1] == AgentEnd(reason="end_turn")


async def _drive(events) -> list:
    return [event async for event in events]
