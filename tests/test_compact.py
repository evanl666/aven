"""Tests for compaction."""

from dataclasses import replace

from aven.core.agent import run
from aven.core.compact import Compactor, estimate_tokens, split_at_turn
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
            self.usage = None

        async def __call__(self, messages):
            return AssistantMessage(text="hi")

    monkeypatch.setattr(cli, "Claude", FakeClaude)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("\n"))
    cli.main(["hi", "--root", str(tmp_path)])

    main_model, summariser_model = built
    assert main_model.get("cache") is True
    assert summariser_model.get("cache") is False
    assert "tools" not in summariser_model, "and no tools either"
