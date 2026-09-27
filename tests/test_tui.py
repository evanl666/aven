"""Tests for the full-screen app, run headless with Textual's pilot."""

import asyncio
import time
from dataclasses import replace

import pytest
from textual.widgets import Button, Input

from aven.harness.compact import Compactor
from aven.harness.messages import AssistantMessage, SummaryMessage, ToolCall, new_id
from aven.harness.session import Session
from aven.harness.tools import ToolResult, tool
from aven.toolkit import file_tools
from aven.terminal.shell import Shell
from aven.terminal.widgets import Note, Reply, ToolLine, UserLine


def streaming(*turns):
    """A model that streams each turn's text, then hands back the message."""
    queue = list(turns)

    async def model(llm_messages):
        text, calls = queue.pop(0)
        for word in text.split(" "):
            yield word + " "
        yield AssistantMessage(
            id=new_id(),
            text=text,
            tool_calls=[replace(c, id=new_id()) for c in calls],
            stop_reason="tool_use" if calls else "end_turn",
        )

    return model


def branch_summariser(text="在另一条分支上读过 Downloads,里面只有一个 pdf。"):
    async def model(_llm_messages):
        return AssistantMessage(text=text, id=new_id())

    return model


@pytest.fixture
def box(tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.pdf").write_text("x")
    return tmp_path


def app_for(box, model, extra_tools=()):
    return Shell(
        session=Session.open(box / "s.jsonl"),
        model=model,
        tools=file_tools(box) + list(extra_tools),
        root=box,
    )


async def ask(app, pilot, text):
    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


def texts(app, kind):
    return [str(w.content) for w in app.query(kind)]


async def test_a_prompt_shows_the_question_and_the_streamed_reply(box):
    app = app_for(box, streaming(("你好,我在。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "在吗")

        assert "› 在吗" in texts(app, UserLine)
        replies = [r.source for r in app.query(Reply)]
        assert replies and "你好,我在。" in replies[-1]


async def test_reversible_work_offers_only_undo(box):
    move = ToolCall(name="move_file", args={"src": "Downloads/a.pdf", "dst": "报销/a.pdf"})
    app = app_for(box, streaming(("归档。", [move]), ("好了。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "归档")

        assert (box / "报销" / "a.pdf").exists()
        assert any("✓" in t for t in texts(app, ToolLine))
        assert app.query_one("#undo", Button).display
        assert not app.query_one("#commit", Button).display
        assert not app.query_one("#discard", Button).display


async def test_irreversible_work_waits_and_offers_commit(box):
    sent = []

    @tool(risk="irreversible", preview="发邮件给 {to}")
    def send_mail(to: str) -> str:
        """Send an email."""
        sent.append(to)
        return "sent"

    call = ToolCall(name="send_mail", args={"to": "boss@corp.com"})
    app = app_for(box, streaming(("发信。", [call]), ("等你确认。", [])), [send_mail])
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "发给老板")

        assert sent == [], "nothing leaves before the person decides"
        assert any("已暂存" in t for t in texts(app, ToolLine))
        assert app.query_one("#commit", Button).display

        app.query_one("#commit", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert sent == ["boss@corp.com"]


async def test_undo_puts_the_files_back_and_rewinds_the_conversation(box):
    move = ToolCall(name="move_file", args={"src": "Downloads/a.pdf", "dst": "报销/a.pdf"})
    app = app_for(box, streaming(("归档。", [move]), ("好了。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "归档")
        before = len(app.session.history())

        await ask(app, pilot, "/undo")

        assert (box / "Downloads" / "a.pdf").exists()
        assert not (box / "报销").exists()
        assert len(app.session.history()) < before, "the conversation went back too"
        assert any("撤销了 1 项" in t for t in texts(app, Note))


async def test_escape_interrupts_without_leaving_the_app(box):
    @tool(name="slow")
    def slow(n: str) -> str:
        """Take a while."""
        time.sleep(0.4)
        return n

    calls = [ToolCall(name="slow", args={"n": x}) for x in "ABC"]
    app = app_for(box, streaming(("慢慢来。", calls), ("完。", [])), [slow])
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#prompt", Input).value = "开始"
        await pilot.press("enter")
        await asyncio.sleep(0.5)
        assert app.busy

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.busy
        assert app.is_running, "Esc stops the task, not the program"
        assert any("已中断" in t for t in texts(app, Note))

        answered = {m.tool_call_id for m in app.session.history() if m.kind == "tool_result"}
        asked = [c.id for m in app.session.history() if m.kind == "assistant" for c in m.tool_calls]
        assert set(asked) <= answered, "the conversation is still well formed"


def _slow_tool(seconds=0.4):
    @tool(name="slow")
    def slow() -> str:
        """Take a while, so there is a window to type into."""
        time.sleep(seconds)
        return "ok"

    return slow


async def test_a_second_prompt_while_busy_is_queued_not_refused(box):
    """It used to say "wait, or press Esc" - and Esc throws away good work."""
    app = app_for(
        box,
        streaming(("等一下。", [ToolCall(name="slow", args={})]), ("两件都做了。", [])),
        [_slow_tool()],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#prompt", Input).value = "第一件"
        await pilot.press("enter")
        await asyncio.sleep(0.1)
        app.query_one("#prompt", Input).value = "等等,还有第二件"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert texts(app, UserLine) == ["› 第一件", "› 等等,还有第二件"]
        assert any("排队" in n for n in texts(app, Note))
        assert [(m.source, m.text) for m in app.session.history() if m.kind == "user"] == [
            ("chat", "第一件"),
            ("steering", "等等,还有第二件"),
        ]


async def test_interrupting_gives_a_queued_message_back_to_the_editor(box):
    """It was never read. Discarding it with the run would be losing their words."""
    app = app_for(
        box,
        streaming(("等一下。", [ToolCall(name="slow", args={})]), ("好。", [])),
        [_slow_tool(1.0)],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#prompt", Input).value = "第一件"
        await pilot.press("enter")
        await asyncio.sleep(0.2)
        app.query_one("#prompt", Input).value = "不对,别动那个目录"
        await pilot.press("enter")
        await asyncio.sleep(0.1)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.query_one("#prompt", Input).value == "不对,别动那个目录"
        assert not any(getattr(m, "source", None) == "steering" for m in app.session.history())


async def test_markup_in_names_is_shown_literally(box):
    """A file called [red]x.md, or a prompt with [b], must not be read as formatting."""
    (box / "Downloads" / "[red]x.md").write_text("x")
    call = ToolCall(name="list_dir", args={"path": "Downloads"})
    app = app_for(box, streaming(("看看。", [call]), ("好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "[b]列一下[/b]")

        assert "› [b]列一下[/b]" in texts(app, UserLine)


async def test_help_and_unknown_commands_answer_in_the_transcript(box):
    app = app_for(box, streaming())
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "/help")
        await ask(app, pilot, "/nope")

        notes = texts(app, Note)
        assert any("/undo" in n for n in notes)
        assert any("没有 /nope" in n for n in notes)


async def test_continuing_a_session_shows_what_was_said(box):
    session = Session.open(box / "s.jsonl")
    from aven.harness.messages import UserMessage

    session.append(UserMessage(text="上次的问题"))
    session.append(AssistantMessage(text="上次的回答"))

    app = Shell(session=Session.open(box / "s.jsonl"), model=streaming(),
                  tools=file_tools(box), root=box)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "› 上次的问题" in texts(app, UserLine)
        assert any("上次的回答" in r.source for r in app.query(Reply))


# --- naming, describing, and moving between branches -------------------------


async def test_name_is_stored_and_shown_in_the_title_bar(box):
    app = app_for(box, streaming(("好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "/name 发票 7 月")

        assert app.session.name == "发票 7 月"
        assert "发票 7 月" in app.sub_title
        assert any("发票 7 月" in n for n in texts(app, Note))


async def test_name_with_no_argument_says_what_it_is_called(box):
    app = app_for(box, streaming(("好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "/name")
        assert any("还没有名字" in n for n in texts(app, Note))

        await ask(app, pilot, "/name 甲")
        await ask(app, pilot, "/name")
        assert any(n == "甲" for n in texts(app, Note))


async def test_session_shows_enough_to_know_which_file_you_are_in(box):
    app = app_for(box, streaming(("好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "在吗")
        await ask(app, pilot, "/session")

        shown = "\n".join(texts(app, Note))
        assert "s.jsonl" in shown
        assert "个分支" in shown


async def test_tree_shows_the_branches(box):
    app = app_for(box, streaming(("好。", []), ("也好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "第一件")
        await ask(app, pilot, "/tree")

        assert any("▸" in n and "第一件" in n for n in texts(app, Note))


async def test_going_back_carries_what_the_branch_learned(box):
    """Otherwise the new branch repeats every file read the old one did."""
    call = ToolCall(name="list_dir", args={"path": "Downloads"})
    app = app_for(box, streaming(("我看看。", [call]), ("有一个 pdf。", [])))
    app.compactor = Compactor(model=branch_summariser())

    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "看看下载目录")
        first = app.session.history()[0]

        await ask(app, pilot, f"/tree {first.id[:6]}")
        await pilot.pause()

        carried = [
            m for m in app.session.history()
            if isinstance(m, SummaryMessage) and m.scope == "branch"
        ]
        assert len(carried) == 1
        assert "另一条分支" in carried[0].text
        assert any("已带过来" in n for n in texts(app, Note))


async def test_going_back_with_compaction_off_still_moves(box):
    """The carry is a bonus. Losing it must not cost the checkout."""
    app = app_for(box, streaming(("好。", [])))
    assert app.compactor is None

    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "第一件")
        first = app.session.history()[0]

        await ask(app, pilot, f"/tree {first.id[:6]}")

        assert app.session.head == first.id


async def test_a_failed_carry_does_not_undo_the_checkout(box):
    app = app_for(box, streaming(("好。", [])))

    async def broken(_messages):
        raise RuntimeError("summariser down")

    app.compactor = Compactor(model=broken)

    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "第一件")
        first = app.session.history()[0]

        await ask(app, pilot, f"/tree {first.id[:6]}")
        await pilot.pause()

        assert app.session.head == first.id, "the move already happened"
        assert any("没能带过来" in n for n in texts(app, Note))


async def test_forking_switches_the_app_to_the_new_file(box):
    app = app_for(box, streaming(("好。", [])))
    async with app.run_test(size=(120, 40)) as pilot:
        await ask(app, pilot, "第一件")
        original = app.session.path

        await ask(app, pilot, "/fork")

        assert app.session.path != original
        assert app.session.path.name.endswith("-fork.jsonl")
        assert "第一件" in original.read_text(), "the original is still there"
