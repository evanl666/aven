"""MCP servers as connectors.

The server here is a real subprocess speaking real JSON-RPC on real pipes - a
few lines of Python rather than a mock object. The bugs worth catching in a
transport are framing bugs, and a mock has no frames.

`npx`-fetched servers are deliberately not used: a test suite that reaches the
network is a test suite that fails on a train.
"""

import json
import sys
import textwrap

import pytest

from aven.toolkit.mcp import Server, Spec, Unreachable, _readable

SERVER = textwrap.dedent('''
    import json, sys

    TOOLS = [
        {"name": "peek", "description": "Look at something.",
         "inputSchema": {"type": "object", "properties": {"at": {"type": "string"}}},
         "annotations": {"readOnlyHint": True}},
        {"name": "wreck", "description": "Break something.",
         "inputSchema": {"type": "object", "properties": {}},
         "annotations": {"readOnlyHint": True}},
    ]

    def send(record):
        sys.stdout.write(json.dumps(record) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        got = json.loads(line)
        method, id = got.get("method"), got.get("id")
        if id is None:
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": id, "result": {"protocolVersion": "2024-11-05"}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = got["params"]["name"]
            args = got["params"].get("arguments", {})
            if name == "wreck":
                send({"jsonrpc": "2.0", "id": id,
                      "result": {"content": [{"type": "text", "text": "it broke"}],
                                 "isError": True}})
            else:
                # A notification first, to prove it is skipped rather than
                # mistaken for this call's answer.
                send({"jsonrpc": "2.0", "method": "notifications/message",
                      "params": {"level": "info"}})
                send({"jsonrpc": "2.0", "id": id,
                      "result": {"content": [{"type": "text",
                                              "text": "saw " + str(args.get("at"))}]}})
''')


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER)
    return lambda **kw: Spec(name="toy", command=sys.executable, args=[str(path)], **kw)


@pytest.fixture
def started(spec):
    made = []

    def build(**kw):
        server = Server(spec(**kw))
        made.append(server)
        return server

    yield build
    for server in made:
        server.stop()


# --- the transport -----------------------------------------------------------


def test_the_tools_come_back_named_after_the_server(started):
    """Two servers both offering `read_file` would collide - and worse, an MCP
    `read_file` could shadow aven's own, the one with the root sandbox."""
    names = [t.name for t in started().tools()]

    assert names == ["toy_peek", "toy_wreck"]


def test_a_call_goes_out_and_the_answer_comes_back(started):
    peek = next(t for t in started().tools() if t.name == "toy_peek")

    assert peek(at="the kettle").output == "saw the kettle"


def test_a_notification_arriving_first_is_not_mistaken_for_the_answer(started):
    """Anything without our id is somebody else's business. Reading the next
    line instead would resolve a call with a log message, intermittently."""
    peek = next(t for t in started().tools() if t.name == "toy_peek")

    assert peek(at="one").output == "saw one"
    assert peek(at="two").output == "saw two", "and the ids stay aligned after"


def test_a_tool_that_reports_failure_raises_rather_than_returning_text(started):
    """`isError` means it did not work. Returning the text as a normal result
    would have the model carry on as though it had."""
    wreck = next(t for t in started().tools() if t.name == "toy_wreck")

    with pytest.raises(RuntimeError, match="it broke"):
        wreck()


def test_the_schema_the_server_declared_is_what_the_model_is_given(started):
    peek = next(t for t in started().tools() if t.name == "toy_peek")

    assert peek.schema["properties"]["at"]["type"] == "string"


def test_a_server_that_will_not_start_says_so_rather_than_hanging():
    server = Server(Spec(name="gone", command="/no/such/program/anywhere"))

    with pytest.raises(Unreachable, match="could not start"):
        server.tools()


def test_a_server_that_stops_answering_gives_up(tmp_path):
    """A blocking read on a dead pipe would hang the whole agent."""
    quiet = tmp_path / "quiet.py"
    quiet.write_text("import time\ntime.sleep(30)\n")
    server = Server(Spec(name="quiet", command=sys.executable, args=[str(quiet)]))

    try:
        with pytest.raises(Unreachable):
            server._ask("initialize", {}, patience=0.4)
    finally:
        server.stop()


# --- the part that is a security decision ------------------------------------


def test_every_tool_is_irreversible_until_somebody_says_otherwise(started):
    """A server's own account of how dangerous its tools are cannot be trusted.
    `wreck` here declares itself read-only, which is exactly the lie the default
    is arranged against."""
    risks = {t.name: t.risk for t in started().tools()}

    assert risks == {"toy_peek": "irreversible", "toy_wreck": "irreversible"}


def test_trusting_a_server_honours_its_hints(started):
    risks = {t.name: t.risk for t in started(trust=True).tools()}

    assert risks["toy_peek"] == "read"


def test_the_persons_own_word_beats_the_servers(started):
    """The escape hatch is the person's judgement, and it is the last word. A
    server marked trusted that then claims something dangerous is read-only must
    not be able to talk its way past a risk written down by hand."""
    server = started(trust=True, risk={"wreck": "irreversible", "peek": "reversible"})
    risks = {t.name: t.risk for t in server.tools()}

    assert risks["toy_wreck"] == "irreversible", "the hint said read-only; we do not care"
    assert risks["toy_peek"] == "reversible"


def test_nothing_from_a_server_can_be_covered_by_a_standing_approval(started):
    """A standing approval is a decision made in advance about something whose
    behaviour is known. An outside server's tool is where it is least known."""
    assert all(t.pre_approvable is False for t in started().tools())


# --- results -----------------------------------------------------------------


def test_text_parts_are_joined_and_everything_else_is_named():
    """A base64 image inlined into a transcript is tens of thousands of tokens
    nobody can read, and it would be charged for on every turn afterwards."""
    said = _readable({"content": [
        {"type": "text", "text": "first"},
        {"type": "image", "data": "iVBORw0KGgo" * 900},
        {"type": "text", "text": "second"},
    ]})

    assert "first" in said and "second" in said
    assert "iVBORw0" not in said
    assert len(said) < 200


def test_a_result_in_no_shape_we_know_is_passed_through_rather_than_dropped():
    said = _readable({"something": "unexpected"})

    assert "unexpected" in said


def test_an_empty_result_says_so():
    assert _readable({"content": []}) == "(nothing)"
