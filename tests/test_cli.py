"""Tests for the aven command, with no key and no network."""

import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

import aven.cli.main as cli
from aven.core.messages import AssistantMessage, ToolCall, new_id


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(cli, "SESSIONS", tmp_path / "sessions")
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.pdf").write_text("hello")
    return tmp_path


def fake_claude(monkeypatch, *replies):
    queue = list(replies)

    class FakeClaude:
        def __init__(self, **_):
            self.usage = SimpleNamespace()

        def __call__(self, messages):
            return replace(queue.pop(0) if len(queue) > 1 else queue[0], id=new_id())

    monkeypatch.setattr(cli, "Claude", FakeClaude)


def keys(monkeypatch, *presses):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(presses) + "\n"))


def test_a_missing_key_fails_before_doing_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    assert cli.main(["hi", "--root", str(tmp_path)]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_a_root_that_is_not_a_directory_is_refused(box, monkeypatch, capsys):
    assert cli.main(["hi", "--root", str(box / "Downloads" / "a.pdf")]) == 1
    assert "不是一个目录" in capsys.readouterr().err


def test_one_prompt_runs_and_then_offers_the_batch(box, monkeypatch, capsys):
    fake_claude(
        monkeypatch,
        AssistantMessage(
            text="归档",
            tool_calls=[
                ToolCall(name="move_file", args={"src": "Downloads/a.pdf", "dst": "archive/a.pdf"})
            ],
            stop_reason="tool_use",
        ),
        AssistantMessage(text="做完了"),
    )
    keys(monkeypatch, "")  # Enter: leave it alone

    assert cli.main(["整理", "--root", str(box)]) == 0

    out = capsys.readouterr().out
    assert "做完了" in out
    assert "1 项已执行(可撤销)" in out
    assert (box / "archive" / "a.pdf").exists(), "reversible work runs during the turn"


def test_pressing_u_rolls_the_files_back(box, monkeypatch, capsys):
    fake_claude(
        monkeypatch,
        AssistantMessage(
            tool_calls=[
                ToolCall(name="move_file", args={"src": "Downloads/a.pdf", "dst": "archive/a.pdf"})
            ],
            stop_reason="tool_use",
        ),
        AssistantMessage(text="done"),
    )
    keys(monkeypatch, "u")

    cli.main(["整理", "--root", str(box)])

    assert (box / "Downloads" / "a.pdf").exists()
    assert not (box / "archive" / "a.pdf").exists()
    assert "撤销了 1 项" in capsys.readouterr().out


def test_the_session_file_survives_the_run(box, monkeypatch):
    fake_claude(monkeypatch, AssistantMessage(text="hi"))
    keys(monkeypatch, "")

    cli.main(["hello", "--root", str(box)])

    written = list((box / "sessions").glob("*.jsonl"))
    assert len(written) == 1
    assert "hello" in written[0].read_text(encoding="utf-8")


def test_continue_reuses_the_most_recent_session(box, monkeypatch):
    fake_claude(monkeypatch, AssistantMessage(text="hi"))
    keys(monkeypatch, "", "")

    cli.main(["first", "--root", str(box)])
    cli.main(["second", "--root", str(box), "-c"])

    written = list((box / "sessions").glob("*.jsonl"))
    assert len(written) == 1, "-c must not start a new file"
    assert "second" in written[0].read_text(encoding="utf-8")


def test_yes_skips_the_prompt_and_commits(box, monkeypatch):
    """--yes is for automation: nothing is asked, staged work fires."""
    fake_claude(monkeypatch, AssistantMessage(text="nothing to do"))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # no input available at all

    assert cli.main(["go", "--root", str(box), "--yes"]) == 0
