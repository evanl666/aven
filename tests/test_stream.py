"""Tests for the two non-interactive outputs.

The contract these hold is narrow and worth stating: stdout is the result and
stderr is the commentary. A caller pipes stdout somewhere and should never have
to recognise our banner in it.
"""

import json

from aven.terminal.stream import Final, Jsonl, as_json
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
from aven.harness.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from aven.harness.tx import Tray
from aven.harness.tx.tray import Entry


def every_event():
    """One of each, so nothing can be added without a json form."""
    call = ToolCall(id="c1", name="list_dir", args={"path": "Downloads"})
    result = ToolResultMessage(tool_call_id="c1", tool_name="list_dir", output="a.pdf")
    reply = AssistantMessage(text="有一个文件", stop_reason="end_turn")
    return [
        AgentStart(prompt="列一下"),
        TurnStart(index=0),
        MessageDelta(text="有一"),
        MessageEnd(message=UserMessage(text="列一下")),
        ToolStart(call=call),
        ToolEnd(call=call, result=result, staged=False),
        TurnEnd(index=0, message=reply),
        AgentEnd(reason="end_turn"),
    ]


def test_every_event_has_a_json_form():
    """A new event type should fail here, not in a caller's parser."""
    kinds = [as_json(event)["type"] for event in every_event()]

    assert kinds == [
        "agent_start",
        "turn_start",
        "message_delta",
        "message_end",
        "tool_start",
        "tool_end",
        "turn_end",
        "agent_end",
    ]


def test_every_record_survives_a_round_trip_through_json():
    for event in every_event():
        assert json.loads(json.dumps(as_json(event), ensure_ascii=False))


def test_a_staged_call_says_so_because_it_did_not_happen():
    call = ToolCall(id="c1", name="send_mail", args={"to": "a@b.c"})
    result = ToolResultMessage(tool_call_id="c1", tool_name="send_mail", output="staged")

    record = as_json(ToolEnd(call=call, result=result, staged=True))

    assert record["staged"] is True


# --- json mode ---------------------------------------------------------------


def test_json_mode_writes_one_line_per_event(capsys):
    sink = Jsonl()
    for event in every_event():
        sink.handle(event)

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(every_event())
    assert all(json.loads(line)["type"] for line in lines)


def test_json_mode_ends_with_what_is_still_waiting(capsys):
    """A caller that never checks would believe an unsent email went out."""
    tray = Tray()
    tray.entries.append(
        Entry(tool="send_mail", args={}, preview="发邮件给 a@b.c",
              risk="irreversible", state="pending")
    )

    Jsonl().close(tray)

    record = json.loads(capsys.readouterr().out.strip())
    assert record["type"] == "tray"
    waiting = record["pending"][0]
    assert waiting["preview"] == "发邮件给 a@b.c"
    assert waiting["risk"] == "irreversible"
    assert waiting["detail"] is None
    assert waiting["id"], "the field a checkbox names what it is approving by"
    assert record["committed"] == 0


# --- print mode --------------------------------------------------------------


def test_print_mode_writes_the_answer_and_nothing_else(capsys):
    sink = Final()
    for event in every_event():
        sink.handle(event)
    sink.handle(MessageEnd(message=AssistantMessage(text="整理好了", stop_reason="end_turn")))
    sink.close(Tray())

    assert capsys.readouterr().out == "整理好了\n"


def test_print_mode_keeps_the_last_reply_that_actually_said_something(capsys):
    """A run can end on a reply that is only tool calls."""
    sink = Final()
    sink.handle(MessageEnd(message=AssistantMessage(text="我看看", stop_reason="tool_use")))
    sink.handle(
        MessageEnd(
            message=AssistantMessage(
                text="", tool_calls=[ToolCall(name="list_dir", args={})], stop_reason="tool_use"
            )
        )
    )
    sink.close(Tray())

    assert capsys.readouterr().out == "我看看\n"


def test_print_mode_reports_staged_work_on_stderr(capsys):
    tray = Tray()
    tray.entries.append(
        Entry(tool="send_mail", args={}, preview="发邮件给 a@b.c",
              risk="irreversible", state="pending")
    )

    sink = Final()
    sink.handle(MessageEnd(message=AssistantMessage(text="写好了", stop_reason="end_turn")))
    sink.close(tray)

    captured = capsys.readouterr()
    assert captured.out == "写好了\n", "stdout carries the answer alone"
    assert "发邮件给 a@b.c" in captured.err


def test_print_mode_flushes_so_a_long_running_caller_sees_each_answer(monkeypatch):
    """print to anything but a terminal is block-buffered, and --watch does not
    exit between turns - so an unflushed answer waits, and a killed watcher loses
    it entirely. Found by running one.
    """
    flushes = []

    class Counting:
        def write(self, text):
            return len(text)

        def flush(self):
            flushes.append(True)

    monkeypatch.setattr("sys.stdout", Counting())
    monkeypatch.setattr("sys.stderr", Counting())

    sink = Final()
    sink.handle(MessageEnd(message=AssistantMessage(text="done", stop_reason="end_turn")))
    sink.close(Tray())

    assert flushes, "the answer has to leave the buffer when it is written"


# --- why the run ended --------------------------------------------------------
#
# A caller that reads stdout gets a confident paragraph either way. The run that
# gave up at a limit has to say so somewhere, or "finished" and "ran out of
# turns mid-sentence" are indistinguishable to everything downstream.


def test_print_mode_says_when_it_stopped_at_the_turn_limit(capsys):
    sink = Final()
    for index in range(3):
        reply = AssistantMessage(text=f"working, step {index}", stop_reason="tool_use")
        sink.handle(MessageEnd(message=reply))
        sink.handle(TurnEnd(index=index, message=reply))
    sink.handle(AgentEnd(reason="max_turns"))
    sink.close(Tray())

    out, err = capsys.readouterr()
    assert out.strip() == "working, step 2", "stdout still carries only the answer"
    assert "limit" in err and "3" in err, err
    assert "incomplete" in err


def test_print_mode_says_when_the_reply_was_cut_off(capsys):
    sink = Final()
    reply = AssistantMessage(text="half a sent", stop_reason="max_tokens")
    sink.handle(MessageEnd(message=reply))
    sink.handle(TurnEnd(index=0, message=reply))
    sink.handle(AgentEnd(reason="truncated"))
    sink.close(Tray())

    out, err = capsys.readouterr()
    assert out.strip() == "half a sent"
    assert "cut off" in err


def test_print_mode_stays_quiet_when_the_work_was_simply_done(capsys):
    """The usual case must not grow a warning nobody needs."""
    sink = Final()
    reply = AssistantMessage(text="done", stop_reason="end_turn")
    sink.handle(MessageEnd(message=reply))
    sink.handle(TurnEnd(index=0, message=reply))
    sink.handle(AgentEnd(reason="end_turn"))
    sink.close(Tray())

    out, err = capsys.readouterr()
    assert out.strip() == "done"
    assert err == "", f"unasked-for commentary: {err!r}"
