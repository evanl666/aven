"""Tests for compaction."""

from dataclasses import replace

from types import SimpleNamespace

import pytest

from aven.core.agent import run
from aven.core.calling import ContextOverflow
from aven.core.compact import Compactor, estimate_tokens, split_at_turn, touched_files
from aven.core.events import MessageEnd
from aven.core.messages import (
    AssistantMessage,
    SummaryMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    new_id,
    to_llm,
)
from aven.core.session import Session
from aven.core.tools import tool


def summariser(text="早先:归档了 3 张发票到 报销/,财务是 finance@corp.com。"):
    """A model that plays the part of the summariser, recording what it saw."""
    seen = []

    async def model(llm_messages):
        seen.append(llm_messages)
        return AssistantMessage(text=text)

    model.seen = seen
    return model


def scripted_stop(stop_reason):
    """A model whose reply ends for a given reason."""

    def model(_llm_messages):
        return AssistantMessage(
            id=new_id(), text="说到一半就", stop_reason=stop_reason
        )

    return model


def conversation(session, turns=5):
    """Build a path of complete turns, each with a tool call and its result."""
    for n in range(turns):
        session.append(UserMessage(text=f"第 {n} 个请求"))
        call = ToolCall(name="echo", args={"n": str(n)})
        session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
        session.append(
            ToolResultMessage(tool_call_id=call.id, tool_name="echo", output=f"结果 {n}")
        )
        session.append(AssistantMessage(text=f"第 {n} 个做完了"))


def well_formed(llm_messages) -> bool:
    """Every tool_use answered by a tool_result, which the API requires."""
    asked, answered = set(), set()
    for message in llm_messages:
        for block in message["content"]:
            if block["type"] == "tool_use":
                asked.add(block["id"])
            elif block["type"] == "tool_result":
                answered.add(block["tool_use_id"])
    return asked <= answered


# --- the pieces --------------------------------------------------------------


def test_the_estimate_counts_cjk_as_heavier_than_ascii():
    """Four ASCII characters to a token; a Chinese character is closer to one."""
    assert estimate_tokens(["x" * 1000]) < 300
    assert estimate_tokens(["中" * 1000]) > 900


def test_the_cut_lands_before_a_user_message(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=5)

    old, keep = split_at_turn(session.history(), keep=2)

    assert isinstance(keep[0], UserMessage), "what is kept must start a turn"
    assert keep[0].text == "第 3 个请求"
    assert len(old) + len(keep) == len(session.history())


def test_nothing_is_cut_when_there_are_too_few_turns(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=2)

    old, keep = split_at_turn(session.history(), keep=4)

    assert old == []
    assert keep == session.history()


def test_a_summary_hides_what_it_covers_and_leads_the_rest(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    first = session.append(UserMessage(text="很久以前"))
    session.append(AssistantMessage(text="回答"))
    recent = session.append(UserMessage(text="刚才"))
    session.append(SummaryMessage(text="早先聊了一些", covers=[first.id]))

    projected = to_llm(session.history())
    said = [b["text"] for m in projected for b in m["content"] if b["type"] == "text"]

    assert "很久以前" not in said, "covered messages are left out"
    assert "回答" in said, "only what the summary covers is hidden"
    assert "刚才" in said
    assert "早先聊了一些" in said[0], "the summary leads, though it was appended last"
    assert recent.id in {m.id for m in session.history()}


def test_a_newer_summary_supersedes_an_older_one(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    old_turn = session.append(UserMessage(text="第一轮"))
    first = session.append(SummaryMessage(text="旧摘要", covers=[old_turn.id]))
    session.append(SummaryMessage(text="新摘要", covers=[old_turn.id, first.id]))

    said = [
        b["text"] for m in to_llm(session.history()) for b in m["content"] if b["type"] == "text"
    ]
    assert any("新摘要" in s for s in said)
    assert not any("旧摘要" in s for s in said)


# --- the compactor -----------------------------------------------------------


async def test_nothing_happens_while_the_context_still_fits(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=5)
    model = summariser()

    assert await Compactor(model=model, limit=1_000_000).maybe_compact(
        session, to_llm(session.history())
    ) is None
    assert model.seen == [], "the summariser was never called"


async def test_too_few_turns_is_left_alone_even_when_too_long(tmp_path):
    """Summarising these would throw away exactly what the model is working on."""
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=2)
    model = summariser()

    assert await Compactor(model=model, limit=1, keep_turns=4).maybe_compact(
        session, to_llm(session.history())
    ) is None
    assert model.seen == []


async def test_compaction_shortens_the_projection_but_not_the_file(tmp_path):
    path = tmp_path / "s.jsonl"
    session = Session.open(path)
    conversation(session, turns=6)
    before_lines = len(path.read_text(encoding="utf-8").splitlines())
    before_tokens = estimate_tokens(to_llm(session.history()))

    summary = await Compactor(model=summariser(), limit=1, keep_turns=2).maybe_compact(
        session, to_llm(session.history())
    )

    assert isinstance(summary, SummaryMessage)
    assert estimate_tokens(to_llm(session.history())) < before_tokens
    assert len(path.read_text(encoding="utf-8").splitlines()) == before_lines + 1
    assert len(session.history()) == before_lines + 1, "nothing left the path either"


async def test_what_is_left_is_still_a_legal_conversation(tmp_path):
    """The one thing compaction must never do is orphan a tool result."""
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)

    await Compactor(model=summariser(), limit=1, keep_turns=2).maybe_compact(
        session, to_llm(session.history())
    )

    assert well_formed(to_llm(session.history()))


async def test_the_summariser_sees_the_old_turns_and_the_instructions(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)
    model = summariser()

    await Compactor(model=model, limit=1, keep_turns=2).maybe_compact(
        session, to_llm(session.history())
    )

    asked = model.seen[0]
    said = [b.get("text", "") for m in asked for b in m["content"]]
    assert any("第 0 个请求" in s for s in said), "the old turns are what it summarises"
    assert not any("第 5 个请求" in s for s in said), "the recent ones are not"
    assert "summarising" in said[-1], "the instructions come last"


# --- inside the loop ---------------------------------------------------------


async def test_the_loop_compacts_before_asking_and_reports_it(tmp_path):
    @tool()
    def echo(n: str) -> str:
        """Echo."""
        return n

    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)

    replies = [AssistantMessage(text="好的")]

    def model(llm_messages):
        model.last = llm_messages
        return replace(replies[0], id=new_id())

    events = [
        event
        async for event in run(
            session=session,
            prompt="继续",
            model=model,
            tools=[echo],
            compactor=Compactor(model=summariser(), limit=1, keep_turns=2),
        )
    ]

    summaries = [
        e.message for e in events
        if isinstance(e, MessageEnd) and isinstance(e.message, SummaryMessage)
    ]
    assert len(summaries) == 1, "the UI is told the conversation was compacted"

    said = [b.get("text", "") for m in model.last for b in m["content"]]
    assert any("早先" in s for s in said), "the model was given the summary"
    assert not any("第 0 个请求" in s for s in said), "and not what it replaced"


def test_the_summariser_does_not_write_to_the_prompt_cache(tmp_path, monkeypatch):
    """Its prefix is the conversation being retired: read once, never again."""
    import aven.cli.main as cli

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(cli, "SESSIONS", tmp_path / "sessions")

    built = []

    class FakeClaude:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.usage = SimpleNamespace(last_input=0)

        async def context_window(self):
            return 200_000

        async def __call__(self, messages):
            return AssistantMessage(text="hi")

    monkeypatch.setattr(cli, "Claude", FakeClaude)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("\n"))
    cli.main(["hi", "--root", str(tmp_path)])

    main_model, summariser_model = built
    assert main_model.get("cache") is True
    assert summariser_model.get("cache") is False
    assert "tools" not in summariser_model, "and no tools either"


# --- recovering from a refusal -----------------------------------------------


def refusing_once(text="好的"):
    """A model that rejects the first request for length, then answers."""
    calls = []

    async def model(llm_messages):
        calls.append(llm_messages)
        if len(calls) == 1:
            raise ContextOverflow("prompt is too long: 250000 tokens > 200000 maximum")
        return AssistantMessage(text=text)

    model.calls = calls
    return model


async def test_a_refusal_for_length_is_recovered_by_compacting(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)
    model = refusing_once()

    events = [
        event
        async for event in run(
            session=session,
            prompt="继续",
            model=model,
            compactor=Compactor(model=summariser(), limit=1_000_000, keep_turns=2),
        )
    ]

    assert len(model.calls) == 2, "asked again after shortening"
    assert len(model.calls[1]) < len(model.calls[0]), "and with less to read"
    assert any(
        isinstance(e, MessageEnd) and isinstance(e.message, SummaryMessage) for e in events
    )
    assert [m.text for m in session.history() if m.kind == "assistant"][-1] == "好的"


async def test_a_second_refusal_is_not_hidden(tmp_path):
    """Compacting twice would only cost another summary and refuse again."""
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)

    async def always_refuses(llm_messages):
        raise ContextOverflow("prompt is too long")
        yield  # pragma: no cover - makes this an async generator

    with pytest.raises(ContextOverflow):
        async for _ in run(
            session=session,
            prompt="继续",
            model=always_refuses,
            compactor=Compactor(model=summariser(), limit=1_000_000, keep_turns=2),
        ):
            pass


async def test_without_a_compactor_the_refusal_travels_on(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    with pytest.raises(ContextOverflow):
        async for _ in run(session=session, prompt="go", model=refusing_once()):
            pass


async def test_insisting_gives_up_the_turns_it_would_normally_keep(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=2)
    careful = Compactor(model=summariser(), limit=1, keep_turns=4)

    assert await careful.compact_now(session) is None, "careful: leaves too few turns alone"
    assert await careful.compact_now(session, insist=True) is not None, "insisting: takes them"


# --- measuring ---------------------------------------------------------------


def test_the_provider_count_is_preferred_over_the_guess(tmp_path):
    """Exact, and free: the number came back with the last reply."""
    compactor = Compactor(model=summariser(), limit=100, measure=lambda _: 150)

    assert compactor.size(["短"]) == 150
    assert compactor.too_long(["短"]) is True


def test_the_guess_covers_the_first_turn(tmp_path):
    """Nothing has been sent yet, so there is nothing for the provider to have
    counted."""
    compactor = Compactor(model=summariser(), limit=100, measure=lambda _: 0)

    assert compactor.size(["中" * 500]) == estimate_tokens(["中" * 500])


async def test_the_window_is_asked_for_once_and_remembered(monkeypatch):
    from aven.model.claude import ASSUMED_WINDOW, Claude

    asked = []

    class Models:
        async def retrieve(self, model_id):
            asked.append(model_id)
            return SimpleNamespace(max_input_tokens=1_000_000)

    client = SimpleNamespace(models=Models(), messages=None)
    model = Claude(client=client)

    assert await model.context_window() == 1_000_000
    assert await model.context_window() == 1_000_000
    assert asked == ["claude-opus-5"], "asked once"
    assert ASSUMED_WINDOW < 1_000_000, "the fallback errs on the small side"


async def test_an_unreachable_models_api_falls_back_to_the_smaller_guess():
    from aven.model.claude import ASSUMED_WINDOW, Claude

    class Models:
        async def retrieve(self, model_id):
            raise RuntimeError("offline")

    model = Claude(client=SimpleNamespace(models=Models(), messages=None))

    assert await model.context_window() == ASSUMED_WINDOW


def test_last_input_is_every_token_that_went_in():
    from aven.model.claude import Usage

    usage = Usage()
    usage.add(
        SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_read_input_tokens=900, cache_creation_input_tokens=90,
        )
    )

    assert usage.last_input == 1000, "uncached, read and written together"

    usage.add(SimpleNamespace(input_tokens=7, output_tokens=1,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))
    assert usage.last_input == 7, "the latest request, not a running total"
    assert usage.input_tokens == 17, "while the totals do accumulate"


# --- files ---------------------------------------------------------------------


def files_conversation(session, names):
    for name in names:
        session.append(UserMessage(text=f"处理 {name}"))
        call = ToolCall(name="move_file", args={"src": f"Downloads/{name}", "dst": f"报销/{name}"})
        session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
        session.append(
            ToolResultMessage(tool_call_id=call.id, tool_name="move_file", output="moved")
        )
        session.append(AssistantMessage(text="好了"))


def test_paths_are_read_out_of_the_calls_not_the_prose(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    files_conversation(session, ["a.pdf", "b.pdf"])

    assert touched_files(session.history()) == [
        "Downloads/a.pdf", "报销/a.pdf", "Downloads/b.pdf", "报销/b.pdf",
    ]


def test_the_same_path_is_listed_once(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    files_conversation(session, ["a.pdf", "a.pdf"])

    assert touched_files(session.history()) == ["Downloads/a.pdf", "报销/a.pdf"]


async def test_the_summariser_is_given_the_paths_verbatim(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    files_conversation(session, ["发票_滴滴.pdf", "发票_美团.pdf"])
    model = summariser()

    summary = await Compactor(model=model, limit=1, keep_turns=1).maybe_compact(
        session, to_llm(session.history())
    )

    instructions = model.seen[0][-1]["content"][0]["text"]
    assert "Downloads/发票_滴滴.pdf" in instructions
    assert "exact paths" in instructions
    assert "Downloads/发票_滴滴.pdf" in summary.files, "and kept on the summary"


async def test_the_list_survives_being_summarised_again(tmp_path):
    """Compaction is cumulative, or it forgets the beginning of long tasks."""
    session = Session.open(tmp_path / "s.jsonl")
    files_conversation(session, ["第一批.pdf", "还有一批.pdf"])

    first = await Compactor(model=summariser(), limit=1, keep_turns=1).maybe_compact(
        session, to_llm(session.history())
    )
    assert "Downloads/第一批.pdf" in first.files

    files_conversation(session, ["第二批.pdf", "再一批.pdf"])
    second = await Compactor(model=summariser(), limit=1, keep_turns=1).maybe_compact(
        session, to_llm(session.history())
    )

    assert "Downloads/第一批.pdf" in second.files, "the earlier batch is still named"
    assert "Downloads/第二批.pdf" in second.files


def test_a_conversation_that_touched_nothing_adds_no_instructions(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="今天天气怎么样"))
    session.append(AssistantMessage(text="我看不到天气"))

    assert touched_files(session.history()) == []


# --- a truncated reply -------------------------------------------------------


async def test_a_truncated_reply_is_reported_rather_than_passed_off_as_finished(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    events = [
        event
        async for event in run(
            session=session,
            prompt="写一篇很长的东西",
            model=scripted_stop("max_tokens"),
        )
    ]

    assert events[-1].reason == "truncated"


async def test_a_truncated_reply_makes_room_for_the_next_turn(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    conversation(session, turns=6)

    events = [
        event
        async for event in run(
            session=session,
            prompt="继续",
            model=scripted_stop("max_tokens"),
            compactor=Compactor(model=summariser(), limit=1, keep_turns=2),
        )
    ]

    assert any(
        isinstance(e, MessageEnd) and isinstance(e.message, SummaryMessage) for e in events
    ), "the context that left no room is shortened"
    assert events[-1].reason == "truncated"


async def test_an_ordinary_finish_still_says_end_turn(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")

    events = [
        event
        async for event in run(
            session=session, prompt="你好", model=scripted_stop("end_turn")
        )
    ]

    assert events[-1].reason == "end_turn"
