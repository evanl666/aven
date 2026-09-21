"""Tests for the Claude adapter, with no key and no network.

The client is injected for the same reason the model function is: an adapter
you can only exercise by spending money is an adapter nobody exercises.
"""

from types import SimpleNamespace

from aven.core.messages import to_llm
from aven.core.tools import tool
from aven.model.claude import Claude, to_assistant


def block(**fields):
    return SimpleNamespace(**fields)


def response(*content, stop_reason="end_turn", **usage):
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 10),
            output_tokens=usage.get("output_tokens", 5),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        ),
    )


class FakeStream:
    """Stands in for the SDK's stream context manager."""

    def __init__(self, reply, chunks):
        self.reply = reply
        self.text_stream = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self.reply


class FakeClient:
    """Records the request, streams whatever it was handed."""

    def __init__(self, *replies, chunks=()):
        self.replies = list(replies)
        self.chunks = list(chunks)
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        return FakeStream(self.replies.pop(0), self.chunks)


def drain(generator):
    """Run a model generator to the end. Returns (chunks, final message)."""
    chunks = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as done:
            return chunks, done.value


def test_text_only_reply():
    msg = to_assistant(response(block(type="text", text="hello")))

    assert msg.text == "hello"
    assert msg.tool_calls == []
    assert msg.stop_reason == "end_turn"


def test_tool_use_keeps_the_providers_id():
    """The tool_result we send back has to quote this id exactly."""
    msg = to_assistant(
        response(
            block(type="text", text="looking"),
            block(type="tool_use", id="toolu_ABC", name="find", input={"dir": "~"}),
            stop_reason="tool_use",
        )
    )

    assert msg.stop_reason == "tool_use"
    assert [(c.id, c.name, c.args) for c in msg.tool_calls] == [
        ("toolu_ABC", "find", {"dir": "~"})
    ]


def test_thinking_is_kept_whole_and_replayed_first():
    """Claude reasons before calling a tool; the next request must carry it back."""
    thought = {"type": "thinking", "thinking": "let me check", "signature": "sig"}
    msg = to_assistant(
        response(
            block(type="thinking", model_dump=lambda exclude_none: thought),
            block(type="text", text="ok"),
            block(type="tool_use", id="t1", name="f", input={}),
            stop_reason="tool_use",
        )
    )

    assert msg.thinking == [thought]

    content = to_llm([msg])[0]["content"]
    assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]


def test_unknown_stop_reason_becomes_error_not_a_normal_finish():
    assert to_assistant(response(stop_reason="something_new")).stop_reason == "error"
    assert to_assistant(response(stop_reason="refusal")).stop_reason == "refusal"


def test_multiple_text_blocks_are_joined():
    msg = to_assistant(response(block(type="text", text="a"), block(type="text", text="b")))
    assert msg.text == "a\nb"


def test_the_request_carries_the_tool_schemas():
    @tool()
    def find(dir: str) -> str:
        """Find things."""
        return "ok"

    client = FakeClient(response(block(type="text", text="hi")))
    model = Claude(tools=[find], system="You are aven.", client=client)

    drain(model([{"role": "user", "content": [{"type": "text", "text": "go"}]}]))
    request = client.requests[0]

    assert request["model"] == "claude-opus-5"
    assert request["system"] == "You are aven."
    assert request["thinking"] == {"type": "adaptive"}
    assert request["tools"] == [find.for_model()]


def test_optional_fields_are_left_out_rather_than_sent_as_none():
    client = FakeClient(response(block(type="text", text="hi")))
    drain(Claude(client=client)([{"role": "user", "content": []}]))

    assert "system" not in client.requests[0]
    assert "tools" not in client.requests[0]


def test_usage_accumulates_across_calls():
    client = FakeClient(
        response(block(type="text", text="a"), input_tokens=100, output_tokens=20),
        response(block(type="text", text="b"), input_tokens=150, output_tokens=30,
                 cache_read_input_tokens=90),
    )
    model = Claude(client=client)

    drain(model([]))
    drain(model([]))

    assert (model.usage.requests, model.usage.input_tokens) == (2, 250)
    assert (model.usage.output_tokens, model.usage.cached_tokens) == (50, 90)


def test_text_arrives_in_pieces_and_the_message_comes_last():
    """The whole point: the caller sees words before the turn is finished."""
    client = FakeClient(
        response(block(type="text", text="我先看看 Downloads。")),
        chunks=["我先", "看看 ", "Downloads。"],
    )

    chunks, message = drain(Claude(client=client)([]))

    assert chunks == ["我先", "看看 ", "Downloads。"]
    assert message.text == "我先看看 Downloads。"


def test_nothing_is_sent_until_the_first_chunk_is_asked_for():
    client = FakeClient(response(block(type="text", text="hi")))

    generator = Claude(client=client)([])

    assert client.requests == [], "a generator does nothing until it is driven"
    drain(generator)
    assert len(client.requests) == 1
