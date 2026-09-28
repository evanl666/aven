"""Tests for the Claude adapter, with no key and no network.

The client is injected for the same reason the model function is: an adapter
you can only exercise by spending money is an adapter nobody exercises.
"""

from types import SimpleNamespace

import pytest

from aven.harness.messages import to_llm
from aven.harness.tools import tool
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
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        ),
    )


class FakeStream:
    """Stands in for the SDK's async stream context manager."""

    def __init__(self, reply, chunks):
        self.reply = reply
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    @property
    def text_stream(self):
        async def chunks():
            for chunk in self._chunks:
                yield chunk

        return chunks()

    async def get_final_message(self):
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


async def drain(generator):
    """Run a model generator to the end. Returns (chunks, final message)."""
    from aven.harness.messages import AssistantMessage

    chunks, message = [], None
    async for item in generator:
        if isinstance(item, AssistantMessage):
            message = item
        else:
            chunks.append(item)
    return chunks, message


async def test_text_only_reply():
    msg = to_assistant(response(block(type="text", text="hello")))

    assert msg.text == "hello"
    assert msg.tool_calls == []
    assert msg.stop_reason == "end_turn"


async def test_tool_use_keeps_the_providers_id():
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


async def test_thinking_is_kept_whole_and_replayed_first():
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


async def test_unknown_stop_reason_becomes_error_not_a_normal_finish():
    assert to_assistant(response(stop_reason="something_new")).stop_reason == "error"
    assert to_assistant(response(stop_reason="refusal")).stop_reason == "refusal"


async def test_multiple_text_blocks_are_joined():
    msg = to_assistant(response(block(type="text", text="a"), block(type="text", text="b")))
    assert msg.text == "a\nb"


async def test_the_request_carries_the_tool_schemas():
    @tool()
    def find(dir: str) -> str:
        """Find things."""
        return "ok"

    client = FakeClient(response(block(type="text", text="hi")))
    model = Claude(tools=[find], system="You are aven.", client=client)

    await drain(model([{"role": "user", "content": [{"type": "text", "text": "go"}]}]))
    request = client.requests[0]

    assert request["model"] == "claude-opus-5"
    assert request["system"] == "You are aven."
    assert request["thinking"] == {"type": "adaptive"}
    assert request["tools"] == [find.for_model()]


async def test_optional_fields_are_left_out_rather_than_sent_as_none():
    client = FakeClient(response(block(type="text", text="hi")))
    await drain(Claude(client=client)([{"role": "user", "content": []}]))

    assert "system" not in client.requests[0]
    assert "tools" not in client.requests[0]


async def test_usage_accumulates_across_calls():
    client = FakeClient(
        response(block(type="text", text="a"), input_tokens=100, output_tokens=20),
        response(block(type="text", text="b"), input_tokens=150, output_tokens=30,
                 cache_read_input_tokens=90),
    )
    model = Claude(client=client)

    await drain(model([]))
    await drain(model([]))

    assert (model.usage.requests, model.usage.input_tokens) == (2, 250)
    assert (model.usage.output_tokens, model.usage.cached_tokens) == (50, 90)


async def test_text_arrives_in_pieces_and_the_message_comes_last():
    """The whole point: the caller sees words before the turn is finished."""
    client = FakeClient(
        response(block(type="text", text="我先看看 Downloads。")),
        chunks=["我先", "看看 ", "Downloads。"],
    )

    chunks, message = await drain(Claude(client=client)([]))

    assert chunks == ["我先", "看看 ", "Downloads。"]
    assert message.text == "我先看看 Downloads。"


async def test_nothing_is_sent_until_the_first_chunk_is_asked_for():
    client = FakeClient(response(block(type="text", text="hi")))

    generator = Claude(client=client)([])

    assert client.requests == [], "a generator does nothing until it is driven"
    await drain(generator)
    assert len(client.requests) == 1


# --- prompt caching ---------------------------------------------------------


async def test_caching_is_on_by_default():
    client = FakeClient(response(block(type="text", text="hi")))
    await drain(Claude(client=client)([]))

    assert client.requests[0]["cache_control"] == {"type": "ephemeral"}


async def test_caching_can_be_turned_off():
    client = FakeClient(response(block(type="text", text="hi")))
    await drain(Claude(cache=False, client=client)([]))

    assert "cache_control" not in client.requests[0]


async def test_written_tokens_are_not_counted_as_hits():
    """The turn that fills the cache paid 1.25x; calling that a hit would make
    the first turn of every session look free."""
    client = FakeClient(
        response(block(type="text", text="a"), input_tokens=2,
                 cache_creation_input_tokens=1000),
        response(block(type="text", text="b"), input_tokens=2,
                 cache_read_input_tokens=1000),
    )
    model = Claude(client=client)

    await drain(model([]))
    assert model.usage.hit_rate == 0.0, "a write is not a hit"

    await drain(model([]))
    assert model.usage.total_input == 2004
    assert model.usage.hit_rate == pytest.approx(1000 / 2004)


async def test_usage_reads_and_writes_are_shown_apart():
    client = FakeClient(
        response(block(type="text", text="a"), input_tokens=2,
                 cache_read_input_tokens=900, cache_creation_input_tokens=100)
    )
    model = Claude(client=client)
    await drain(model([]))

    assert "cache read 900 · wrote 100" in str(model.usage)


# --- the provider saying no --------------------------------------------------


def refusal(kind):
    """An SDK error, built without its constructor.

    That constructor wants a real response object from whichever HTTP library
    the SDK happens to be built on this month - it is `httpx2` today. What is
    being tested is that we catch the class and translate it, and the response
    is no part of that, so depending on it would be coupling a test of ours to
    somebody else's packaging.
    """
    made = kind.__new__(kind)
    Exception.__init__(made, "refused")
    return made


def refusing(problem):
    """A Claude whose every request comes back as `problem`."""

    class Messages:
        def stream(self, **_):
            raise problem

    return Claude(client=SimpleNamespace(messages=Messages()))


async def test_a_rejected_key_is_a_sentence_rather_than_a_traceback():
    """The most likely way a first run ends. A stack trace at that moment reads
    as a bug in aven and buries the one line saying what to go and fix."""
    import anthropic

    from aven.harness.calling import Unreachable

    model = refusing(refusal(anthropic.AuthenticationError))

    with pytest.raises(Unreachable, match="ANTHROPIC_API_KEY"):
        async for _ in model([{"role": "user", "content": "hi"}]):
            pass


async def test_being_out_of_credit_says_so_rather_than_saying_429():
    """429 is what the wire says. It is not what a person needs to read."""
    import anthropic

    from aven.harness.calling import Unreachable

    model = refusing(refusal(anthropic.RateLimitError))

    with pytest.raises(Unreachable, match="credit"):
        async for _ in model([{"role": "user", "content": "hi"}]):
            pass


async def test_a_refusal_we_have_no_words_for_is_not_swallowed():
    """Only the four that have something useful to say are translated. Anything
    else must keep its traceback, because that one really is a bug."""
    from aven.harness.calling import Unreachable

    model = refusing(ValueError("something else entirely"))

    with pytest.raises(ValueError):
        async for _ in model([{"role": "user", "content": "hi"}]):
            pass
    assert not issubclass(ValueError, Unreachable)
