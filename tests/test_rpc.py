"""Tests for aven driven by another program.

The interesting half is not "does prompt work". It is the concurrency and the
lifetimes: what a second prompt does while the first is running, whether the tray
survives between prompts, whether `agent_end` is enough to re-enable an input box,
and what a malformed command does to the connection.

`Conversation` takes an `emit` callback and never touches a pipe, so all of this
runs without a subprocess. The stdio loop is tested separately and thinly, which
is the right split - the protocol is where the thinking is and the pipe is where
the buffering bugs are.
"""

import asyncio
import io
import json
from dataclasses import replace

import pytest

from aven.harness.messages import AssistantMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.harness.toolbox import ToolBox
from aven.harness.tools import Order, ToolResult, tool
from aven.harness.tx import Approval, Standing, guard, protect
from aven.wire import protocol
from aven.wire.rpc import Conversation

# --- a world to drive --------------------------------------------------------

ran: list[str] = []


@tool(risk="read")
def look() -> str:
    """Look at something."""
    ran.append("look")
    return "looked"


@tool(risk="reversible", preview="change {path}")
def change(path: str) -> ToolResult:
    """Change a file."""
    ran.append(f"change:{path}")
    return ToolResult(output="changed", undo=lambda: ran.append(f"undo:{path}"))


@tool(risk="irreversible", preview="buy {what}",
      detail=lambda what, **_: Order(items=[(what, "¥38")], total="¥38", where="Amazon"),
      pre_approvable=False)
def buy(what: str) -> ToolResult:
    """Buy something."""
    ran.append(f"buy:{what}")
    return ToolResult(output="bought")


@tool(risk="read")
def list_events() -> str:
    """List calendar events."""
    return "nothing today"


def scripted(*replies):
    queue = list(replies)

    def model(_messages):
        return replace(queue.pop(0) if len(queue) > 1 else queue[0], id=new_id())

    model.usage = "0 requests"
    return model


def calling(name, **args):
    return AssistantMessage(tool_calls=[ToolCall(name=name, args=args)],
                            stop_reason="tool_use")


def done(text="finished"):
    return AssistantMessage(text=text, stop_reason="end_turn")


@pytest.fixture(autouse=True)
def clean():
    ran.clear()
    yield


@pytest.fixture
def talking(tmp_path):
    """A conversation with a recorded event stream."""
    sent: list[dict] = []
    box = ToolBox(core=[look, change, buy], groups={"calendar": [list_events]})

    def build(model=None, **kwargs):
        return Conversation(
            session=Session.open(tmp_path / "s.jsonl"),
            model=model or scripted(done()),
            box=box,
            describe={"calendar": "Read and create calendar events."},
            sessions_dir=tmp_path,
            emit=sent.append,
            **kwargs,
        )

    build.sent = sent
    build.box = box
    return build


def kinds(sent):
    return [record["type"] for record in sent]


async def settled(conversation):
    """Wait for the run to be over, the way a client waits for `settled`.

    The extra turn of the loop is not padding. `settled` is emitted from the
    task's done callback, which asyncio schedules with call_soon - so awaiting
    the task returns before the callback has written anything.
    """
    if conversation.task is not None:
        with _quiet():
            await conversation.task
    await asyncio.sleep(0)


class _quiet:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True  # a cancelled or failed run is the test's business, not ours


# --- unknown and malformed ---------------------------------------------------


async def test_an_unknown_command_is_a_response_not_a_crash(talking):
    """A dispatcher that raises would take the connection down over a typo, and
    the client could not tell that from aven crashing."""
    reply = await talking().handle({"type": "nonsense", "id": "7"})

    assert reply["ok"] is False
    assert reply["id"] == "7"
    assert "nonsense" in reply["error"]


async def test_a_command_that_fails_inside_comes_back_as_a_response(talking):
    reply = await talking().handle({"type": "connect", "group": "nope"})

    assert reply["ok"] is False
    assert "nope" in reply["error"]


async def test_an_id_is_echoed_so_a_client_can_correlate(talking):
    """Handling is asynchronous, so a client correlates by id and not by order."""
    reply = await talking().handle({"type": "state", "id": "abc"})

    assert reply["id"] == "abc"


async def test_a_command_with_no_id_gets_a_response_with_none(talking):
    assert "id" not in await talking().handle({"type": "state"})


# --- talking ----------------------------------------------------------------


async def test_a_prompt_starts_a_run_and_the_events_arrive(talking):
    conversation = talking(scripted(calling("look"), done("had a look")))

    reply = await conversation.handle({"type": "prompt", "message": "look at it"})
    await settled(conversation)

    assert reply["data"]["disposition"] == "started"
    assert "agent_start" in kinds(talking.sent)
    assert "tool_end" in kinds(talking.sent)
    assert "agent_end" in kinds(talking.sent)
    assert ran == ["look"]


async def test_a_prompt_response_means_accepted_not_finished(talking):
    """The events are what say finished. A client that treats ok as done draws a
    completed turn before the first token.

    With a model that awaits, which is every real one - a synchronous stub
    finishes the whole run inside the single loop turn that starts the task.
    """
    conversation = talking(_slow_model())

    reply = await conversation.handle({"type": "prompt", "message": "hello"})

    assert reply["data"]["disposition"] == "started"
    assert "agent_start" in kinds(talking.sent), "it really has begun"
    assert "agent_end" not in kinds(talking.sent), "and not finished"

    await conversation.handle({"type": "interrupt"})
    await settled(conversation)


async def test_settled_is_the_signal_and_agent_end_is_not(talking):
    """A follow-up carries the run past agent_end, so a client re-enabling its
    input box on agent_end would re-enable it mid-run."""
    conversation = talking(scripted(done()))
    await conversation.handle({"type": "prompt", "message": "hello"})
    await settled(conversation)

    assert kinds(talking.sent)[-1] == "settled"
    assert kinds(talking.sent).index("agent_end") < len(kinds(talking.sent)) - 1


async def test_a_second_prompt_while_busy_is_queued_not_refused(talking):
    """What the full-screen app does, and what a chat window wants."""
    slow = _slow_model()
    conversation = talking(slow)
    await conversation.handle({"type": "prompt", "message": "first"})

    reply = await conversation.handle({"type": "prompt", "message": "second"})

    assert reply["ok"] is True
    assert reply["data"]["disposition"] == "queued"
    assert reply["data"]["queued"] == 1
    await settled(conversation)
    said = [m.text for m in conversation.session.history() if m.kind == "user"]
    assert said == ["first", "second"]


async def test_steering_is_explicit_and_works_when_nothing_is_running(talking):
    conversation = talking(scripted(done()))

    reply = await conversation.handle({"type": "steer", "message": "also this"})

    assert reply["data"]["queued"] == 1


async def test_an_empty_prompt_is_refused(talking):
    reply = await talking().handle({"type": "prompt", "message": "   "})

    assert reply["ok"] is False


# --- interrupting -----------------------------------------------------------


async def test_interrupting_cancels_the_run_and_returns_what_was_queued(talking):
    """The queued text was never read, so it goes back to the client rather than
    being thrown away with the run that would have read it."""
    conversation = talking(_slow_model())
    await conversation.handle({"type": "prompt", "message": "first"})
    await conversation.handle({"type": "prompt", "message": "wrong folder actually"})

    reply = await conversation.handle({"type": "interrupt"})
    await settled(conversation)

    assert reply["data"]["interrupted"] is True
    assert reply["data"]["returned"] == ["wrong folder actually"]
    assert "interrupted" in kinds(talking.sent)


async def test_interrupting_when_nothing_runs_is_not_an_error(talking):
    reply = await talking().handle({"type": "interrupt"})

    assert reply["ok"] is True
    assert reply["data"]["interrupted"] is False


# --- deciding ---------------------------------------------------------------


async def test_staged_work_reaches_the_client_with_an_id(talking):
    """The id is the field this whole format exists for: a checkbox has to name
    what it is approving."""
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    waiting = (await conversation.handle({"type": "pending"}))["data"]["pending"]

    assert len(waiting) == 1
    assert waiting[0]["id"]
    assert waiting[0]["detail"]["kind"] == "order"
    assert ran == [], "and it has not happened"


async def test_the_tray_is_pushed_while_the_run_is_still_going(talking):
    """Before agent_end, not only after it.

    The run also pushes the tray when it finishes, so asserting a tray record
    exists somewhere proves nothing - a pane that only learns at the end may as
    well have polled.
    """
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    order = kinds(talking.sent)
    assert order.index("tray") < order.index("agent_end")


async def test_approving_some_fires_only_those(talking):
    conversation = talking(
        scripted(
            AssistantMessage(
                tool_calls=[ToolCall(name="buy", args={"what": "a kettle"}),
                            ToolCall(name="buy", args={"what": "a mug"})],
                stop_reason="tool_use",
            ),
            done(),
        )
    )
    await conversation.handle({"type": "prompt", "message": "buy both"})
    await settled(conversation)
    waiting = conversation.tray.pending()

    reply = await conversation.handle({"type": "approve", "ids": [waiting[1].id]})

    assert ran == ["buy:a mug"]
    assert len(reply["data"]["committed"]) == 1
    assert len(reply["data"]["tray"]["pending"]) == 1, "the other one still waits"


async def test_approving_with_no_ids_at_all_fires_everything(talking):
    """"Approve everything" is a thing a client offers."""
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    await conversation.handle({"type": "approve"})

    assert ran == ["buy:a kettle"]


async def test_an_empty_id_list_fires_nothing(talking):
    """Not the same as absent, and must not be read as it."""
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    await conversation.handle({"type": "approve", "ids": []})

    assert ran == []
    assert len(conversation.tray.pending()) == 1


async def test_discarding_drops_what_waits(talking):
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    reply = await conversation.handle({"type": "discard"})

    assert reply["data"]["discarded"] == 1
    assert ran == []


async def test_undo_rolls_the_world_and_the_conversation_back(talking):
    conversation = talking(scripted(calling("change", path="a.md"), done()))
    await conversation.handle({"type": "prompt", "message": "change it"})
    await settled(conversation)
    before = len(conversation.session.history())

    reply = await conversation.handle({"type": "undo"})

    assert reply["data"]["undone"] == 1
    assert "undo:a.md" in ran
    assert len(conversation.session.history()) < before


# --- the tray's lifetime ----------------------------------------------------


async def test_the_tray_survives_between_prompts(talking):
    """A tray made per prompt would drop what the turn before staged, and the
    whole point of a separate approvals surface is coming back to it."""
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)

    conversation.model = scripted(done("nothing to do"))
    await conversation.handle({"type": "prompt", "message": "anything else?"})
    await settled(conversation)

    assert len(conversation.tray.pending()) == 1, "still waiting on me"


async def test_a_settled_tray_is_replaced_so_it_does_not_grow_forever(talking):
    conversation = talking(scripted(calling("change", path="a.md"), done()))
    await conversation.handle({"type": "prompt", "message": "change it"})
    await settled(conversation)
    first = conversation.tray

    await conversation.handle({"type": "undo"})

    assert conversation.tray is not first


# --- connecting -------------------------------------------------------------


async def test_the_connectors_are_listed_with_what_they_would_be_able_to_do(talking):
    """Somebody deciding whether to connect something is entitled to see that."""
    listed = (await talking().handle({"type": "state"}))["data"]["connectors"]

    assert listed == [
        {
            "name": "calendar",
            "about": "Read and create calendar events.",
            # Nothing to sign in to, so it is usable the moment it is asked for.
            "state": "ready",
            "connected": False,
            "tools": ["list_events"],
            "keeps": "",
            "trouble": None,
        }
    ]


async def test_connecting_brings_the_tools_into_play(talking):
    conversation = talking()
    assert "list_events" not in conversation.state()["tools"]

    reply = await conversation.handle({"type": "connect", "group": "calendar"})

    assert reply["data"]["name"] == "calendar"
    assert reply["data"]["state"] == "ready"
    assert "list_events" in conversation.state()["tools"]
    assert reply["data"]["connectors"][0]["connected"] is True


async def test_connecting_twice_says_so_rather_than_failing(talking):
    conversation = talking()
    await conversation.handle({"type": "connect", "group": "calendar"})

    reply = await conversation.handle({"type": "connect", "group": "calendar"})

    assert reply["ok"] is True
    assert reply["data"]["already"] is True


async def test_a_service_connected_mid_conversation_is_callable(talking):
    """The reason rpc takes the box rather than a resolved tool list."""
    conversation = talking(scripted(calling("list_events"), done()))
    await conversation.handle({"type": "connect", "group": "calendar"})

    await conversation.handle({"type": "prompt", "message": "what is on today"})
    await settled(conversation)

    assert "agent_end" in kinds(talking.sent)
    results = [m.output for m in conversation.session.history() if m.kind == "tool_result"]
    assert "nothing today" in results


# --- reading ----------------------------------------------------------------


async def test_state_is_enough_to_draw_a_fresh_client(talking):
    reported = (await talking().handle({"type": "state"}))["data"]

    assert reported["version"] == protocol.VERSION
    assert set(reported) >= {
        "session", "busy", "queued", "tools", "connectors", "tray", "standing", "usage"
    }


def test_usage_travels_as_numbers_and_not_only_as_a_sentence():
    """A client with a narrow place to put this must not have to parse prose.

    The sentence is translated, so a window that pulled the numbers back out of
    it with a regex would break the first time the wording or the language
    changed. Both forms go on the wire and the client picks.
    """

    class Counted:
        requests = 3
        total_input = 12_400
        output_tokens = 900
        cached_tokens = 8_000

        def __str__(self) -> str:
            return "3 requests · in 12400 · out 900"

    sent = protocol.usage_as_dict(Counted())

    assert sent["input"] == 12_400
    assert sent["output"] == 900
    assert sent["cached"] == 8_000
    assert sent["requests"] == 3
    assert sent["line"] == "3 requests · in 12400 · out 900"


def test_usage_before_the_first_request_is_zeroed_not_the_word_none():
    """A model that has not run yet has no usage object. `str(None)` would put
    the literal text "None" in a client's status line."""
    sent = protocol.usage_as_dict(None)

    assert sent == {"requests": 0, "input": 0, "output": 0, "cached": 0, "line": ""}


async def test_the_standing_approvals_are_visible_to_a_client(talking):
    """Something that lets work happen unasked has to be visible on every
    surface, not only in the terminal banner."""
    conversation = talking(standing=Standing([Approval(tool="buy", when="kettle")]))

    assert conversation.state()["standing"] == [
        "buy where the preview contains 'kettle'"
    ]


async def test_the_transcript_comes_back_for_a_client_that_just_connected(talking):
    conversation = talking(scripted(done("hello there")))
    await conversation.handle({"type": "prompt", "message": "hi"})
    await settled(conversation)

    said = (await conversation.handle({"type": "transcript"}))["data"]["messages"]

    assert [m["kind"] for m in said] == ["user", "assistant"]
    assert said[0]["text"] == "hi"


async def test_the_tree_comes_back_as_nodes(talking):
    conversation = talking(scripted(done()))
    await conversation.handle({"type": "prompt", "message": "hi"})
    await settled(conversation)

    nodes = (await conversation.handle({"type": "tree"}))["data"]["nodes"]

    assert nodes[0]["kind"] == "user"
    assert nodes[-1]["is_head"] is True


async def test_naming_reads_and_writes(talking):
    conversation = talking()

    assert (await conversation.handle({"type": "name"}))["data"]["name"] is None
    await conversation.handle({"type": "name", "name": "invoices"})
    assert conversation.session.name == "invoices"


async def test_the_sessions_can_be_listed(talking):
    conversation = talking(scripted(done()))
    await conversation.handle({"type": "prompt", "message": "hi"})
    await settled(conversation)

    listed = (await conversation.handle({"type": "sessions"}))["data"]["sessions"]

    assert listed and listed[0]["path"].endswith("s.jsonl")


async def test_checking_out_replaces_the_tray_with_the_branch(talking):
    """Its undos point at work done on a path we are no longer on."""
    conversation = talking(scripted(calling("change", path="a.md"), done()))
    await conversation.handle({"type": "prompt", "message": "change it"})
    await settled(conversation)
    first = conversation.session.history()[0]

    reply = await conversation.handle({"type": "checkout", "id": first.id[:6]})

    assert reply["data"]["head"] == first.id
    assert reply["data"]["tray"]["undoable"] == []


async def test_checking_out_while_running_is_refused(talking):
    conversation = talking(_slow_model())
    await conversation.handle({"type": "prompt", "message": "hi"})

    reply = await conversation.handle({"type": "checkout", "id": "abc"})

    assert reply["ok"] is False
    await conversation.handle({"type": "interrupt"})
    await settled(conversation)


async def test_shutdown_stops_the_loop_and_the_run(talking):
    conversation = talking(_slow_model())
    await conversation.handle({"type": "prompt", "message": "hi"})

    reply = await conversation.handle({"type": "shutdown"})
    await settled(conversation)

    assert reply["data"]["stopped"] is True
    assert conversation.stopped is True


# --- a run that fails -------------------------------------------------------


async def test_a_run_that_raises_tells_the_client_rather_than_going_quiet(talking):
    """Silence is indistinguishable from thinking."""
    def broken(_messages):
        raise RuntimeError("the provider fell over")

    broken.usage = ""
    conversation = talking(broken)
    await conversation.handle({"type": "prompt", "message": "hi"})
    await settled(conversation)

    assert "failed" in kinds(talking.sent)
    assert kinds(talking.sent)[-1] == "settled", "and it still settles"


# --- every shape crosses json ----------------------------------------------


async def test_every_record_emitted_survives_json(talking):
    conversation = talking(scripted(calling("buy", what="a kettle"), done()))
    await conversation.handle({"type": "prompt", "message": "buy it"})
    await settled(conversation)
    await conversation.handle({"type": "state"})

    for record in talking.sent:
        assert json.loads(json.dumps(record, ensure_ascii=False)) == record


async def test_every_response_survives_json(talking):
    conversation = talking(scripted(done()))
    for command in ({"type": "state"}, {"type": "pending"}, {"type": "tree"},
                    {"type": "sessions"}, {"type": "name"}, {"type": "transcript"}):
        reply = await conversation.handle(command)
        assert json.loads(json.dumps(reply, ensure_ascii=False)) == reply


def _slow_model():
    async def model(_messages):
        await asyncio.sleep(0.2)
        yield AssistantMessage(text="eventually", id=new_id(), stop_reason="end_turn")

    model.usage = ""
    return model


# --- the pipe ---------------------------------------------------------------
#
# Thin on purpose. The protocol is where the thinking is; this is where the
# framing and buffering bugs are, and those are the only things worth asserting.


async def feed(lines: list[str], conversation, monkeypatch, capsys) -> list[dict]:
    """Run the stdio loop over a scripted stdin, and read back what it wrote."""
    from aven.wire.rpc import serve_stdio

    monkeypatch.setattr("sys.stdin", io.StringIO("".join(f"{line}\n" for line in lines)))
    await serve_stdio(conversation)
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


async def test_a_command_per_line_gets_a_response_per_line(talking, monkeypatch, capsys):
    written = await feed(
        ['{"type": "state", "id": "1"}', '{"type": "pending", "id": "2"}'],
        talking(), monkeypatch, capsys,
    )

    assert [r["id"] for r in written] == ["1", "2"]
    assert all(r["ok"] for r in written)


async def test_a_line_that_is_not_json_is_reported_and_the_loop_goes_on(
    talking, monkeypatch, capsys
):
    """One bad line must not end the connection - the client would have no way to
    tell a typo from aven dying."""
    written = await feed(
        ["{ not json", '{"type": "state", "id": "after"}'], talking(), monkeypatch, capsys
    )

    assert written[0]["ok"] is False and written[0]["command"] == "parse"
    assert written[1]["id"] == "after", "and the next command still worked"


async def test_a_json_value_that_is_not_an_object_is_refused(talking, monkeypatch, capsys):
    written = await feed(["[1, 2, 3]"], talking(), monkeypatch, capsys)

    assert written[0]["ok"] is False


async def test_blank_lines_are_skipped(talking, monkeypatch, capsys):
    written = await feed(["", "   ", '{"type": "state"}'], talking(), monkeypatch, capsys)

    assert len(written) == 1


async def test_closing_stdin_is_an_orderly_shutdown(talking, monkeypatch, capsys):
    """Which is how a Tauri sidecar is stopped."""
    conversation = talking()

    assert await _serve_empty(conversation, monkeypatch) == 0


async def _serve_empty(conversation, monkeypatch) -> int:
    from aven.wire.rpc import serve_stdio

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return await serve_stdio(conversation)


def test_every_record_is_flushed_as_it_is_written(monkeypatch):
    """A client reads this as a stream. A block-buffered pipe would hold a turn's
    events until the next one filled it - the bug that ate a --watch answer."""
    from aven.wire.rpc import write

    flushes = []

    class Counting:
        def write(self, text):
            return len(text)

        def flush(self):
            flushes.append(True)

    monkeypatch.setattr("sys.stdout", Counting())
    write({"type": "state"})

    assert flushes


# --- connecting a service that needs signing in -------------------------------


class Slowly:
    """An auth that blocks, the way a browser consent screen does."""

    def __init__(self, fails=None):
        self.gate = asyncio.Event()
        self.held = False
        self.fails = fails

    def ready(self):
        return self.held

    def sign_in(self):
        # Blocks until the test lets it through, which is what a person reading
        # a consent screen looks like from here.
        while not self.gate.is_set():
            import time
            time.sleep(0.005)
        if self.fails:
            raise RuntimeError(self.fails)
        self.held = True

    def forget(self):
        was, self.held = self.held, False
        return was

    def about(self):
        return "a keychain"


def signing(tmp_path, auth):
    from aven.harness.connect import Connections, Connector

    sent = []
    connections = Connections([
        Connector(name="g", about="A service.", tools=[list_events], auth=auth)
    ])
    box = ToolBox(core=[look], groups=connections.groups(), gate=connections.gate)
    return Conversation(
        session=Session.open(tmp_path / "s.jsonl"),
        model=scripted(done()),
        box=box,
        connections=connections,
        sessions_dir=tmp_path,
        emit=sent.append,
    ), sent


async def test_connecting_something_that_needs_a_browser_answers_at_once(tmp_path):
    """Somebody has to read a consent screen, and thirty seconds is not a round
    trip. A client left waiting on the reply would look frozen."""
    auth = Slowly()
    conversation, sent = signing(tmp_path, auth)

    reply = await conversation.handle({"type": "connect", "group": "g"})

    assert reply["ok"] is True
    assert reply["data"]["state"] == "signing_in"
    assert "list_events" not in conversation.state()["tools"], "not yet, either"

    auth.gate.set()
    await conversation.signing["g"]


async def test_the_client_is_told_when_the_sign_in_finishes(tmp_path):
    auth = Slowly()
    conversation, sent = signing(tmp_path, auth)
    await conversation.handle({"type": "connect", "group": "g"})

    auth.gate.set()
    await conversation.signing["g"]

    told = [record for record in sent if record["type"] == "connector"]
    assert told and told[-1]["state"] == "ready"
    assert "list_events" in conversation.state()["tools"], "and the tools are in play"


async def test_a_refused_sign_in_comes_back_as_an_event_with_the_reason(tmp_path):
    auth = Slowly(fails="you said no")
    conversation, sent = signing(tmp_path, auth)
    await conversation.handle({"type": "connect", "group": "g"})

    auth.gate.set()
    await conversation.signing["g"]

    told = [record for record in sent if record["type"] == "connector"][-1]
    assert told["state"] == "needs_sign_in"
    assert "you said no" in told["error"]
    assert "list_events" not in conversation.state()["tools"]


async def test_the_agent_keeps_answering_while_a_sign_in_is_in_flight(tmp_path):
    """`sign_in` blocks on a socket waiting for a redirect. On the event loop it
    would freeze every other command - including the interrupt somebody would
    reach for when they changed their mind about connecting."""
    auth = Slowly()
    conversation, _ = signing(tmp_path, auth)
    await conversation.handle({"type": "connect", "group": "g"})

    answered = await asyncio.wait_for(
        conversation.handle({"type": "state"}), timeout=2
    )

    assert answered["ok"] is True
    auth.gate.set()
    await conversation.signing["g"]


async def test_asking_twice_does_not_open_a_second_browser(tmp_path):
    auth = Slowly()
    conversation, _ = signing(tmp_path, auth)
    await conversation.handle({"type": "connect", "group": "g"})

    again = await conversation.handle({"type": "connect", "group": "g"})

    assert again["data"]["state"] == "signing_in"
    assert len(conversation.signing) == 1
    auth.gate.set()
    await conversation.signing["g"]


async def test_disconnecting_forgets_the_credential_and_takes_the_tools_away(tmp_path):
    """Either half on its own is a lie: tools without a token give the model
    things to call that now fail, and a token without tools means somebody who
    clicked disconnect still has a credential in their keychain."""
    auth = Slowly()
    auth.gate.set()
    conversation, _ = signing(tmp_path, auth)
    await conversation.handle({"type": "connect", "group": "g"})
    await conversation.signing["g"]
    assert "list_events" in conversation.state()["tools"]

    reply = await conversation.handle({"type": "disconnect", "group": "g"})

    assert reply["data"]["forgotten"] is True
    assert reply["data"]["state"] == "needs_sign_in"
    assert auth.held is False
    assert "list_events" not in conversation.state()["tools"]


async def test_disconnecting_something_unknown_is_a_refusal_not_a_crash(tmp_path):
    conversation, _ = signing(tmp_path, Slowly())

    reply = await conversation.handle({"type": "disconnect", "group": "nope"})

    assert reply["ok"] is False


# --- starting without a key ---------------------------------------------------


def keyless(tmp_path):
    """A conversation that has no key yet and somewhere to put one."""
    kept: dict[str, str] = {}
    sent: list[dict] = []
    conversation = Conversation(
        session=Session.open(tmp_path / "s.jsonl"),
        model=scripted(done()),
        box=ToolBox(core=[look], groups={}),
        sessions_dir=tmp_path,
        has_key=lambda: "key" in kept,
        keep_key=lambda key: kept.__setitem__("key", key),
        keeps="the macOS login keychain",
        emit=sent.append,
    )
    return conversation, kept


async def test_a_client_can_see_that_there_is_no_key_yet(tmp_path):
    """A window launched from the dock has no shell, so it cannot be told to
    export one. It has to be able to ask, which means starting first."""
    conversation, _ = keyless(tmp_path)

    state = (await conversation.handle({"type": "state"}))["data"]

    assert state["key"] is False
    assert state["keeps"] == "the macOS login keychain", (
        "a screen that asks for a secret has to say where it is about to put it"
    )


async def test_supplying_a_key_stores_it_and_the_state_says_so(tmp_path):
    conversation, kept = keyless(tmp_path)

    reply = await conversation.handle({"type": "set_key", "key": "sk-ant-abc"})

    assert reply["ok"] is True
    assert reply["data"]["key"] is True
    assert kept["key"] == "sk-ant-abc"
    assert (await conversation.handle({"type": "state"}))["data"]["key"] is True


async def test_an_empty_key_is_refused_rather_than_stored(tmp_path):
    """Otherwise the window would go to its main screen and every prompt would
    fail with something far less clear than this."""
    conversation, kept = keyless(tmp_path)

    reply = await conversation.handle({"type": "set_key", "key": "   "})

    assert reply["ok"] is False
    assert kept == {}


async def test_the_key_never_appears_in_an_event_or_the_response(tmp_path):
    """It is a secret. The response says it worked, and nothing repeats it."""
    conversation, _ = keyless(tmp_path)

    reply = await conversation.handle({"type": "set_key", "key": "sk-ant-secret"})
    state = (await conversation.handle({"type": "state"}))["data"]

    assert "sk-ant-secret" not in json.dumps(reply)
    assert "sk-ant-secret" not in json.dumps(state)


async def test_a_conversation_with_nowhere_to_put_a_key_says_so(tmp_path):
    """Rather than reporting success and losing it."""
    conversation = Conversation(
        session=Session.open(tmp_path / "s.jsonl"),
        model=scripted(done()),
        box=ToolBox(core=[look], groups={}),
        sessions_dir=tmp_path,
        has_key=lambda: False,
        emit=lambda _: None,
    )

    reply = await conversation.handle({"type": "set_key", "key": "sk-ant-abc"})

    assert reply["ok"] is False
    assert "nowhere" in reply["error"]
