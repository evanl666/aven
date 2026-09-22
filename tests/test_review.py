"""Tests for the approval step."""

import io

import pytest

from aven.cli.review import menu, review
from aven.core.tools import ToolResult, tool
from aven.tx import Tray


@tool(risk="reversible", preview="日历「工作」新建:team event")
def done_work() -> ToolResult:
    return ToolResult(output="created", undo=lambda: None)


@tool(risk="irreversible", preview="发邮件给 boss@corp.com")
def waiting_work() -> str:
    return "sent"


def tray_with(*tools) -> Tray:
    tray = Tray()
    for one in tools:
        tray.execute(one, {})
    return tray


def keys(monkeypatch, *presses):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(presses) + "\n"))


def test_finished_work_alone_offers_only_undo():
    """What a calendar event leaves behind: it is already in Calendar."""
    line, allowed = menu(tray_with(done_work))

    assert allowed == {"u"}
    assert "[c]" not in line and "[d]" not in line
    assert "[u] 撤销已执行" in line


def test_staged_work_alone_offers_no_undo():
    line, allowed = menu(tray_with(waiting_work))

    assert allowed == {"c", "d"}
    assert "[u]" not in line


def test_both_kinds_offer_everything():
    _, allowed = menu(tray_with(done_work, waiting_work))
    assert allowed == {"c", "d", "u"}


async def test_an_unoffered_key_is_refused_rather_than_ignored(monkeypatch, capsys):
    """A mistyped key that quietly exits looks exactly like one that worked."""
    tray = tray_with(done_work)
    keys(monkeypatch, "c", "")

    await review(tray)

    out = capsys.readouterr().out
    assert "这里没有 [c] 这个选项" in out
    assert tray.undoable(), "nothing happened to the finished work"


async def test_enter_leaves_everything_as_it_is(monkeypatch):
    tray = tray_with(done_work, waiting_work)
    keys(monkeypatch, "")

    await review(tray)

    assert len(tray.pending()) == 1
    assert len(tray.undoable()) == 1


async def test_undo_empties_the_tray_and_ends_the_loop(monkeypatch):
    tray = tray_with(done_work)
    keys(monkeypatch, "u")

    await review(tray)

    assert not tray.undoable()
