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


# --- a server somewhere else --------------------------------------------------


class Somewhere:
    """A remote MCP server, with the awkward bits turned on: a session id that
    must come back on every later request, and SSE-framed replies."""

    def __init__(self, session="s-1", frames="sse"):
        import http.server
        import threading

        held = self
        self.session = session
        self.frames = frames
        self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_DELETE(self):  # noqa: N802
                held.seen.append(("DELETE", dict(self.headers)))
                self.send_response(200)
                self.end_headers()

            def do_POST(self):  # noqa: N802
                got = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                held.seen.append((got.get("method"), dict(self.headers)))
                method, id = got.get("method"), got.get("id")

                if method != "initialize" and held.session:
                    if self.headers.get("Mcp-Session-Id") != held.session:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(b'{"error": "no session"}')
                        return

                if id is None:
                    self.send_response(202)
                    self.end_headers()
                    return

                if method == "initialize":
                    result = {"protocolVersion": "2024-11-05"}
                elif method == "tools/list":
                    result = {"tools": [{
                        "name": "peek", "description": "Look.",
                        "inputSchema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": True},
                    }]}
                else:
                    result = {"content": [{"type": "text", "text": "from far away"}]}

                answer = {"jsonrpc": "2.0", "id": id, "result": result}
                if held.frames == "sse":
                    noise = {"jsonrpc": "2.0", "method": "notifications/x", "params": {}}
                    body = (
                        b"data: " + json.dumps(noise).encode() + b"\n\n"
                        b"data: " + json.dumps(answer).encode() + b"\n\n"
                    )
                    kind = "text/event-stream"
                else:
                    body = json.dumps(answer).encode()
                    kind = "application/json"

                self.send_response(200)
                self.send_header("Content-Type", kind)
                if method == "initialize" and held.session:
                    self.send_header("Mcp-Session-Id", held.session)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/mcp"

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def somewhere():
    made = []

    def build(**kw):
        one = Somewhere(**kw)
        made.append(one)
        return one

    yield build
    for one in made:
        one.stop()


def remote(one, **kw):
    from aven.toolkit.mcp import Remote

    return Remote(Spec(name="far", url=one.url, **kw))


def test_a_remote_server_is_reached_and_its_tools_used(somewhere):
    server = remote(somewhere())

    tools = server.tools()

    assert [t.name for t in tools] == ["far_peek"]
    assert tools[0](**{}).output == "from far away"


def test_a_reply_framed_as_an_event_stream_is_read(somewhere):
    """Streamable HTTP lets the server choose, so both have to work."""
    assert remote(somewhere(frames="sse")).tools()[0](**{}).output == "from far away"


def test_a_reply_framed_as_plain_json_is_read(somewhere):
    assert remote(somewhere(frames="json")).tools()[0](**{}).output == "from far away"


def test_a_notification_in_the_stream_is_not_mistaken_for_the_answer(somewhere):
    """The stream carries whatever the server feels like sending. Only the
    record whose id matches the request is the answer to it."""
    assert remote(somewhere(frames="sse")).tools()[0](**{}).output == "from far away"


def test_the_session_is_carried_on_every_request_after_the_first(somewhere):
    """It arrives as a header on the initialize reply and is required after.
    Without it the second request looks like a stranger and is refused - which
    is what this stand-in does, so the assertion is that nothing raised."""
    one = somewhere(session="s-abc")
    server = remote(one)

    server.tools()[0](**{})

    assert server.session == "s-abc"
    after = [headers for method, headers in one.seen if method != "initialize"]
    assert after and all(h.get("Mcp-Session-Id") == "s-abc" for h in after)


def test_a_credential_is_sent_and_fetched_fresh_each_time(somewhere):
    """Per request, so a token that expired mid-conversation is refreshed
    rather than sent stale."""
    asked = []

    class Token:
        def token(self):
            asked.append(1)
            return f"token-{len(asked)}"

    one = somewhere()
    server = remote(one, auth=Token())
    server.tools()[0](**{})

    sent = [h.get("Authorization") for _, h in one.seen]
    assert sent[0] == "Bearer token-1"
    assert len(set(sent)) > 1, "not captured once and reused"


def test_a_remote_server_gets_the_same_safe_default(somewhere):
    """It declares readOnlyHint, and distance makes that less trustworthy
    rather than more: it is somebody else's computer."""
    assert remote(somewhere()).tools()[0].risk == "irreversible"


def test_trusting_a_remote_server_works_the_same_way(somewhere):
    assert remote(somewhere(), trust=True).tools()[0].risk == "read"


def test_a_server_that_is_not_there_says_so(somewhere):
    from aven.toolkit.mcp import Remote

    server = Remote(Spec(name="far", url="http://127.0.0.1:9/mcp"))

    with pytest.raises(Unreachable, match="could not reach"):
        server.tools()


def test_disconnecting_lets_the_server_drop_the_session(somewhere):
    one = somewhere()
    server = remote(one)
    server.tools()

    server.stop()

    assert any(method == "DELETE" for method, _ in one.seen), (
        "the server should be told, so it can drop the session rather than "
        "holding it until it expires"
    )
    assert server.session is None
    assert server.listed == []


def test_the_transport_is_chosen_by_how_it_is_configured():
    from aven.toolkit.mcp import Remote, Server, open_server

    assert isinstance(open_server(Spec(name="a", command="x")), Server)
    assert isinstance(open_server(Spec(name="b", url="https://x/mcp")), Remote)


def test_a_name_that_already_carries_the_prefix_does_not_get_it_twice(tmp_path):
    """A server called `browser` whose tools are `browser_click` would become
    `browser_browser_click` - noise in every request for as long as the group is
    loaded. The prefix is there so two servers cannot collide and so nothing can
    shadow a built-in; a name already carrying it does both."""
    from aven.toolkit.mcp import Server

    server = Server(Spec(name="browser", command="x"))

    assert server._named("browser_click") == "browser_click"
    assert server._named("click") == "browser_click"


def test_a_merely_similar_name_still_gets_the_prefix():
    """`browserify` is not `browser_`, and treating it as one would let a tool
    called `browsering` through unprefixed."""
    from aven.toolkit.mcp import Server

    server = Server(Spec(name="browser", command="x"))

    assert server._named("browserify") == "browser_browserify"
