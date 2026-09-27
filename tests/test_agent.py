"""Tests for the agent loop."""

from dataclasses import replace

from aven.harness.agent import run
from aven.harness.events import (
    AgentEnd,
    AgentStart,
    MessageDelta,
    MessageEnd,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from aven.harness.messages import AssistantMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.harness.tools import ToolResult, tool


async def drive(events) -> list:
    """Collect an async event stream into a list."""
    return [event async for event in events]


def scripted(*replies):
    """A model that returns canned replies, then repeats the last one.

    Each reply is a fresh copy: a real model never hands back a message that is
    already in the session, and Session.append refuses one that is.
    """
    queue = list(replies)

    def model(llm_messages):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return replace(reply, id=new_id())

    return model


def streaming(*chunks, reply):
    """A model that streams text and ends with the finished message."""

    async def model(llm_messages):
        for chunk in chunks:
            yield chunk
        yield replace(reply, id=new_id())

    return model


@tool()
def echo(x: str) -> str:
    """Echo a string back."""
    return x


@tool(name="echo")
def echo0() -> str:
    """Echo with no arguments."""
    return "x"


async def test_a_plain_answer_is_one_turn(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    events = await drive(run(session=s, prompt="hi", model=scripted(AssistantMessage(text="hello"))))

    assert [type(e).__name__ for e in events] == [
        "AgentStart", "MessageEnd", "TurnStart", "MessageEnd", "TurnEnd", "AgentEnd",
    ]
    assert events[-1].reason == "end_turn"
    assert [m.text for m in s.history()] == ["hi", "hello"]


async def test_tool_call_feeds_back_into_a_second_turn(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={"x": "ok"})
    model = scripted(
        AssistantMessage(text="working", tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    )

    events = await drive(run(session=s, prompt="go", model=model, tools=[echo]))

    assert sum(isinstance(e, TurnStart) for e in events) == 2
    tool_end = next(e for e in events if isinstance(e, ToolEnd))
    assert tool_end.result.output == "ok"
    assert tool_end.result.is_error is False
    assert [m.kind for m in s.history()] == ["user", "assistant", "tool_result", "assistant"]


async def test_a_crashing_tool_becomes_an_errored_result(tmp_path):
    """A tool_use with no tool_result is a malformed conversation."""
    s = Session.open(tmp_path / "s.jsonl")

    @tool(name="boom")
    def boom():
        raise RuntimeError("nope")

    call = ToolCall(name="boom", args={})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="recovered"),
    )

    events = await drive(run(session=s, prompt="go", model=model, tools=[boom]))
    result = next(e for e in events if isinstance(e, ToolEnd)).result

    assert result.is_error is True
    assert "RuntimeError: nope" in result.output
    assert events[-1].reason == "end_turn", "the loop keeps going after a tool fails"


async def test_unknown_tool_is_reported_to_the_model_not_raised(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="ghost", args={})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="ok"),
    )

    events = await drive(run(session=s, prompt="go", model=model))
    result = next(e for e in events if isinstance(e, ToolEnd)).result

    assert result.is_error is True
    assert "no such tool" in result.output


async def test_max_turns_stops_a_runaway_model(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    model = scripted(
        AssistantMessage(tool_calls=[ToolCall(name="echo", args={})], stop_reason="tool_use")
    )

    events = await drive(
        run(session=s, prompt="go", model=model, tools=[echo0], max_turns=3)
    )

    assert sum(isinstance(e, TurnStart) for e in events) == 3
    assert events[-1].reason == "max_turns"


async def test_every_appended_message_emits_exactly_one_message_end(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={"x": "1"})
    model = scripted(
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    )

    events = await drive(run(session=s, prompt="go", model=model, tools=[echo]))
    ended = [e.message.id for e in events if isinstance(e, MessageEnd)]

    assert ended == [m.id for m in s.history()]


async def test_the_model_sees_the_current_path_growing(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    seen = []
    call = ToolCall(name="echo", args={"x": "1"})
    replies = [
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    ]

    def model(llm_messages):
        seen.append(len(llm_messages))
        return replies.pop(0)

    await drive(run(session=s, prompt="go", model=model, tools=[echo]))
    assert seen == [1, 3], "turn 2 sees the user turn, the tool call, and its result"


# --- streaming --------------------------------------------------------------


async def test_a_streaming_model_produces_deltas_before_the_message(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    model = streaming(
        "我先", "看看 ", "Downloads。", reply=AssistantMessage(text="我先看看 Downloads。")
    )

    events = await drive(run(session=s, prompt="go", model=model))

    assert [type(e).__name__ for e in events] == [
        "AgentStart", "MessageEnd",
        "TurnStart", "MessageDelta", "MessageDelta", "MessageDelta", "MessageEnd",
        "TurnEnd", "AgentEnd",
    ]
    assert "".join(e.text for e in events if isinstance(e, MessageDelta)) == "我先看看 Downloads。"


async def test_the_streamed_message_is_the_one_stored(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    model = streaming("part", reply=AssistantMessage(text="the whole thing"))

    await drive(run(session=s, prompt="go", model=model))

    assert [m.text for m in s.history()] == ["go", "the whole thing"]


async def test_a_non_streaming_model_produces_no_deltas(tmp_path):
    """Streaming is a property of the model, not of the loop."""
    s = Session.open(tmp_path / "s.jsonl")

    events = await drive(
        run(session=s, prompt="go", model=scripted(AssistantMessage(text="hi")))
    )

    assert not any(isinstance(e, MessageDelta) for e in events)


async def test_an_async_model_that_returns_a_message_works_too(tmp_path):
    """A plain `async def` returning a message, with no streaming at all."""
    s = Session.open(tmp_path / "s.jsonl")

    async def model(llm_messages):
        return AssistantMessage(text="hi")

    events = await drive(run(session=s, prompt="go", model=model))

    assert not any(isinstance(e, MessageDelta) for e in events)
    assert [m.text for m in s.history()] == ["go", "hi"]


async def test_a_streaming_model_can_still_call_tools(tmp_path):
    s = Session.open(tmp_path / "s.jsonl")
    call = ToolCall(name="echo", args={"x": "ok"})
    turns = [
        AssistantMessage(text="working", tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="done"),
    ]

    async def model(llm_messages):
        yield "th"
        yield "inking"
        yield replace(turns.pop(0), id=new_id())

    events = await drive(run(session=s, prompt="go", model=model, tools=[echo]))

    assert sum(isinstance(e, TurnStart) for e in events) == 2
    assert sum(isinstance(e, MessageDelta) for e in events) == 4, "both turns streamed"
    assert [m.kind for m in s.history()] == ["user", "assistant", "tool_result", "assistant"]


async def test_a_slow_tool_does_not_block_the_event_loop(tmp_path):
    """Tools run in a thread, so the loop stays free to do other work."""
    import asyncio

    ticks = 0

    @tool(name="slow")
    def slow() -> str:
        import time

        time.sleep(0.25)
        return "done"

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    s = Session.open(tmp_path / "s.jsonl")
    model = scripted(
        AssistantMessage(tool_calls=[ToolCall(name="slow", args={})], stop_reason="tool_use"),
        AssistantMessage(text="ok"),
    )

    ticker = asyncio.create_task(tick())
    await drive(run(session=s, prompt="go", model=model, tools=[slow]))
    ticker.cancel()

    assert ticks > 5, "the event loop kept running while the tool slept"


# --- interruption ------------------------------------------------------------


def _unanswered(session):
    answered = {m.tool_call_id for m in session.history() if m.kind == "tool_result"}
    return [
        c.args for m in session.history() if m.kind == "assistant"
        for c in m.tool_calls if c.id not in answered
    ]


def _three_slow_calls():
    import time

    @tool(name="slow")
    def slow(n: str) -> str:
        time.sleep(0.2)
        return n

    calls = [ToolCall(name="slow", args={"n": x}) for x in "ABC"]
    return slow, scripted(AssistantMessage(tool_calls=calls, stop_reason="tool_use"))


async def test_cancelling_mid_batch_leaves_no_tool_use_unanswered(tmp_path):
    """Esc in the UI cancels the task; the conversation must stay well formed."""
    import asyncio

    slow, model = _three_slow_calls()
    s = Session.open(tmp_path / "s.jsonl")

    async def consume():
        async for _ in run(session=s, prompt="go", model=model, tools=[slow]):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert _unanswered(s) == []
    interrupted = [m for m in s.history() if m.kind == "tool_result" and m.is_error]
    assert interrupted and "interrupted" in interrupted[0].output


async def test_a_caller_that_stops_listening_leaves_none_unanswered(tmp_path):
    """The approval-gate pattern: break at ToolStart, never ask for more."""
    slow, model = _three_slow_calls()
    s = Session.open(tmp_path / "s.jsonl")

    events = run(session=s, prompt="go", model=model, tools=[slow])
    async for event in events:
        if isinstance(event, ToolStart):
            break
    await events.aclose()

    assert _unanswered(s) == []


# --- a reply that was cut off mid-write --------------------------------------
#
# stop_reason max_tokens means the model stopped in the middle of writing. Two
# things follow, and both used to be got wrong: the person was left holding half
# a sentence, and the call it was halfway through writing was run anyway.


def _ran(session):
    """Which tools actually produced a result, in order."""
    return [
        m.tool_name for m in session.history()
        if m.kind == "tool_result" and not m.is_error
    ]


async def test_a_truncated_reply_is_asked_to_finish(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    events = await drive(
        run(
            session=session,
            prompt="写点长的",
            model=scripted(
                AssistantMessage(text="说到一半就", stop_reason="max_tokens"),
                AssistantMessage(text="接着说完了", stop_reason="end_turn"),
            ),
        )
    )

    assert events[-1] == AgentEnd(reason="end_turn"), "the run finishes properly"
    assert [m.text for m in session.history() if m.kind == "assistant"] == [
        "说到一半就",
        "接着说完了",
    ]


async def test_the_nudge_to_finish_is_marked_as_the_loop_s_own(tmp_path):
    """It is a user message on the wire, but no person typed it."""
    session = Session.open(tmp_path / "s.jsonl")

    await drive(
        run(
            session=session,
            prompt="写点长的",
            model=scripted(
                AssistantMessage(text="说到一半就", stop_reason="max_tokens"),
                AssistantMessage(text="写完了", stop_reason="end_turn"),
            ),
        )
    )

    sources = [m.source for m in session.history() if m.kind == "user"]
    assert sources == ["chat", "resume"]


async def test_a_reply_that_keeps_truncating_is_resumed_only_once(tmp_path):
    """Otherwise an answer too big for max_tokens costs a context per round."""
    session = Session.open(tmp_path / "s.jsonl")

    events = await drive(
        run(
            session=session,
            prompt="写点长的",
            model=scripted(AssistantMessage(text="说到一半就", stop_reason="max_tokens")),
        )
    )

    resumes = [m for m in session.history() if m.kind == "user" and m.source == "resume"]
    assert len(resumes) == 1
    assert events[-1] == AgentEnd(reason="truncated")


async def test_the_call_a_truncated_reply_was_still_writing_is_not_run(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    reply = AssistantMessage(
        tool_calls=[
            ToolCall(name="echo", args={"x": "finished"}),
            ToolCall(name="echo", args={"x": "half-writ"}),
        ],
        stop_reason="max_tokens",
    )
    await drive(
        run(
            session=session,
            prompt="做两件事",
            model=scripted(reply, AssistantMessage(text="好了", stop_reason="end_turn")),
            tools=[echo],
        )
    )

    results = [m for m in session.history() if m.kind == "tool_result"]
    assert [r.output for r in results][:1] == ["finished"]
    assert results[1].is_error, "the call it never finished writing comes back failed"
    assert "Issue it again" in results[1].output


async def test_a_truncated_reply_still_answers_every_call_it_made(tmp_path):
    """The invariant that outranks all of this: no tool_use without a result."""
    session = Session.open(tmp_path / "s.jsonl")

    await drive(
        run(
            session=session,
            prompt="做两件事",
            model=scripted(
                AssistantMessage(
                    tool_calls=[
                        ToolCall(name="echo", args={"x": "a"}),
                        ToolCall(name="echo", args={"x": "b"}),
                    ],
                    stop_reason="max_tokens",
                ),
                AssistantMessage(text="好了", stop_reason="end_turn"),
            ),
            tools=[echo],
        )
    )

    assert _unanswered(session) == []


async def test_a_finished_reply_runs_every_call_it_made(tmp_path):
    """The control: only max_tokens holds a call back."""
    session = Session.open(tmp_path / "s.jsonl")

    await drive(
        run(
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
                AssistantMessage(text="好了", stop_reason="end_turn"),
            ),
            tools=[echo],
        )
    )

    assert _ran(session) == ["echo", "echo"]
