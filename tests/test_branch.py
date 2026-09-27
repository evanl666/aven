"""Tests for carrying what a branch learned onto the branch you move to.

/tree made this a real gap rather than a theoretical one. Before it, nobody
could leave a branch, so nothing was ever lost by leaving one.

The distinction the whole feature turns on is `scope`. A compaction summary
stands in for messages on this path, hides them, and leads the projection. A
branch summary describes messages on another path, hides nothing, and stays
where it was appended - because that is when it was learned.
"""

from aven.core.compact import Compactor
from aven.core.messages import (
    AssistantMessage,
    SummaryMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    to_llm,
)
from aven.core.session import Session


def summariser(text="试过了:发票在 Downloads/invoices/,PDF 是扫描件,读不出文字。"):
    seen = []

    async def model(llm_messages):
        seen.append(llm_messages)
        return AssistantMessage(text=text)

    model.seen = seen
    return model


def attempt(session, ask, reply, path="Downloads/a.pdf"):
    """A turn that asked, read a file, and answered."""
    session.append(UserMessage(text=ask))
    call = ToolCall(id="c" + str(len(session)), name="read_file", args={"path": path})
    session.append(AssistantMessage(tool_calls=[call], stop_reason="tool_use"))
    session.append(
        ToolResultMessage(tool_call_id=call.id, tool_name="read_file", output="...")
    )
    return session.append(AssistantMessage(text=reply, stop_reason="end_turn"))


# --- writing it --------------------------------------------------------------


async def test_what_a_branch_found_out_lands_on_the_branch_you_move_to(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    start = session.append(UserMessage(text="整理发票"))
    attempt(session, "先看看", "看不出内容")
    left = session.head

    session.checkout(start.id)
    carried = await Compactor(model=summariser()).summarise_branch(session, left)

    assert carried is not None
    assert carried.scope == "branch"
    assert carried.covers == [], "it stands in for nothing; it only adds"
    assert carried in session.history(), "and it is on the branch we moved to"


async def test_the_paths_the_branch_touched_come_with_it(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    start = session.append(UserMessage(text="整理发票"))
    attempt(session, "先看看", "看不出内容", path="Downloads/invoices/03.pdf")
    left = session.head

    session.checkout(start.id)
    carried = await Compactor(model=summariser()).summarise_branch(session, left)

    assert "Downloads/invoices/03.pdf" in carried.files


async def test_a_branch_nothing_was_tried_on_carries_nothing(tmp_path):
    """Stepping onto a branch point and straight off it is the common case."""
    session = Session.open(tmp_path / "s.jsonl")
    start = session.append(UserMessage(text="整理发票"))
    left = session.append(UserMessage(text="想了想算了")).id

    session.checkout(start.id)
    model = summariser()
    carried = await Compactor(model=model).summarise_branch(session, left)

    assert carried is None
    assert model.seen == [], "and no request was paid for"


async def test_the_summariser_reads_the_branch_being_left_not_the_current_one(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    start = session.append(UserMessage(text="整理发票"))
    attempt(session, "分支A:试试按日期", "按日期分好了")
    left = session.head

    session.checkout(start.id)
    attempt(session, "分支B:改成按金额", "按金额分好了")

    model = summariser()
    await Compactor(model=model).summarise_branch(session, left)

    asked = repr(model.seen[0])
    assert "分支A" in asked
    assert "分支B" not in asked, "the branch we are on is not what is being summarised"


# --- how the model reads it --------------------------------------------------


def test_a_branch_summary_is_told_it_is_another_attempt(tmp_path):
    """Handed over unframed, it reads as work already done here."""
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="整理发票"))
    session.append(SummaryMessage(text="试过按日期分,分好了", scope="branch"))

    projected = repr(to_llm(session.history()))

    assert "abandoned_branch" in projected
    assert "不一定还在" in projected, "and that its changes may not stand"


def test_a_branch_summary_stays_where_it_was_appended(tmp_path):
    """It was learned at that point. Leading the projection would misdate it."""
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="第一句"))
    session.append(SummaryMessage(text="另一条分支的结论", scope="branch"))
    session.append(UserMessage(text="第二句"))

    texts = [m["content"][0]["text"] for m in to_llm(session.history())]

    assert texts[0] == "第一句"
    assert "另一条分支的结论" in texts[1]
    assert texts[2] == "第二句"


def test_a_compaction_summary_still_leads_the_projection(tmp_path):
    """The other scope, unchanged: it describes what came before all of this."""
    session = Session.open(tmp_path / "s.jsonl")
    first = session.append(UserMessage(text="早先说的"))
    session.append(UserMessage(text="后来说的"))
    session.append(SummaryMessage(text="早先的摘要", covers=[first.id]))

    texts = [m["content"][0]["text"] for m in to_llm(session.history())]

    assert "早先的摘要" in texts[0]
    assert texts[1] == "后来说的"
    assert "早先说的" not in texts, "covered, so it is gone from the projection"


def test_a_branch_summary_hides_nothing(tmp_path):
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="还在的"))
    session.append(SummaryMessage(text="分支结论", scope="branch"))

    texts = [m["content"][0]["text"] for m in to_llm(session.history())]

    assert "还在的" in texts


def test_a_session_written_before_scope_existed_still_reads(tmp_path):
    """Every file on disk says nothing about scope, and must mean "earlier"."""
    from aven.core.messages import from_dict

    restored = from_dict({"kind": "summary", "id": "a1", "text": "旧的摘要", "covers": []})

    assert restored.scope == "earlier"


async def test_a_branch_summary_can_itself_be_compacted_away(tmp_path):
    """It is an ordinary message. Nothing about it is exempt."""
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="第一轮"))
    session.append(SummaryMessage(text="分支结论", scope="branch"))
    session.append(AssistantMessage(text="好", stop_reason="end_turn"))
    session.append(UserMessage(text="第二轮"))
    session.append(AssistantMessage(text="好", stop_reason="end_turn"))

    await Compactor(model=summariser("压缩了"), limit=1, keep_turns=1).compact_now(session)

    texts = repr(to_llm(session.history()))
    assert "分支结论" not in texts, "it was covered like anything else"


async def test_compacting_a_branch_summary_does_not_claim_it_was_a_compaction(tmp_path):
    """The CARRIED instruction is about a previous compaction, not a branch."""
    from aven.core.compact import CARRIED

    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="第一轮"))
    session.append(SummaryMessage(text="分支结论", scope="branch"))
    session.append(AssistantMessage(text="好", stop_reason="end_turn"))
    session.append(UserMessage(text="第二轮"))
    session.append(AssistantMessage(text="好", stop_reason="end_turn"))

    model = summariser("压缩了")
    await Compactor(model=model, limit=1, keep_turns=1).compact_now(session)

    assert CARRIED.strip() not in repr(model.seen[0])
