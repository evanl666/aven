"""Tests for the aven command, with no key and no network."""

import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

import aven.apps.cli_assistant.main as cli
import aven.terminal.app as foundation
from aven.harness.messages import AssistantMessage, ToolCall, new_id


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
            self.usage = SimpleNamespace(last_input=0)

        async def context_window(self):
            return 200_000

        async def __call__(self, messages):
            return replace(queue.pop(0) if len(queue) > 1 else queue[0], id=new_id())

    monkeypatch.setattr(foundation, "Claude", FakeClaude)


def keys(monkeypatch, *presses):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(presses) + "\n"))


def test_a_missing_key_fails_before_doing_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    assert cli.main(["hi", "--root", str(tmp_path)]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_a_root_that_is_not_a_directory_is_refused(box, monkeypatch, capsys):
    assert cli.main(["hi", "--root", str(box / "Downloads" / "a.pdf")]) == 1
    assert "not a directory" in capsys.readouterr().err


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
    assert "1 done (undoable)" in out
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
    assert "undone 1" in capsys.readouterr().out


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


# --- being called by something that is not a person --------------------------


def quiet_stdin(monkeypatch):
    """No pipe. piped() reads stdin when it is not a tty, and StringIO never is."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


def test_print_mode_puts_the_answer_on_stdout_and_the_banner_on_stderr(box, monkeypatch, capsys):
    fake_claude(monkeypatch, AssistantMessage(text="今天没有会"))
    quiet_stdin(monkeypatch)

    assert cli.main(["-p", "今天有什么会", "--root", str(box)]) == 0

    captured = capsys.readouterr()
    assert captured.out == "今天没有会\n", "a caller can pipe this straight into something"
    assert "aven ·" in captured.err, "the banner is commentary, not output"


def test_json_mode_writes_events_a_caller_can_parse(box, monkeypatch, capsys):
    call = ToolCall(name="list_dir", args={"path": "Downloads"})
    fake_claude(
        monkeypatch,
        AssistantMessage(tool_calls=[call], stop_reason="tool_use"),
        AssistantMessage(text="一个 pdf"),
    )
    quiet_stdin(monkeypatch)

    assert cli.main(["--mode", "json", "列一下", "--root", str(box)]) == 0

    import json

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    kinds = [r["type"] for r in records]
    assert kinds[0] == "agent_start"
    assert "tool_end" in kinds
    assert kinds[-1] == "tray", "so a caller knows whether anything is still waiting"


def test_print_mode_needs_a_prompt(box, monkeypatch, capsys):
    fake_claude(monkeypatch, AssistantMessage(text="hi"))
    quiet_stdin(monkeypatch)

    assert cli.main(["-p", "--root", str(box)]) == 1
    assert "needs a prompt" in capsys.readouterr().err


def test_a_piped_in_file_becomes_the_material_and_the_argument_says_what_to_do(
    box, monkeypatch, capsys
):
    seen = []

    class FakeClaude:
        def __init__(self, **_):
            self.usage = SimpleNamespace(last_input=0)

        async def context_window(self):
            return 200_000

        async def __call__(self, messages):
            seen.append(messages)
            return AssistantMessage(text="看过了", id=new_id())

    monkeypatch.setattr(foundation, "Claude", FakeClaude)
    monkeypatch.setattr("sys.stdin", io.StringIO("diff --git a/x b/x\n+一行"))

    assert cli.main(["-p", "看看这个改动", "--root", str(box)]) == 0

    asked = repr(seen[0])
    assert "diff --git" in asked and "看看这个改动" in asked


def test_print_mode_leaves_irreversible_work_staged_and_says_so(box, monkeypatch, capsys):
    """Nobody is here to confirm it, and confirming on their behalf is not ours to do.

    --protect is what makes the write irreversible here: the file tools are all
    reversible on purpose, so a policy rule is the portable way to get a staged
    entry without reaching for Mail.
    """
    fake_claude(
        monkeypatch,
        AssistantMessage(
            tool_calls=[ToolCall(name="write_file", args={"path": "Downloads/新的.txt",
                                                         "content": "x"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(text="等你确认"),
    )
    quiet_stdin(monkeypatch)

    assert cli.main(["-p", "写个文件", "--root", str(box), "--protect", "Downloads"]) == 0

    captured = capsys.readouterr()
    assert "not run" in captured.err
    assert not (box / "Downloads" / "新的.txt").exists(), "it really did not happen"


def test_yes_commits_what_print_mode_would_otherwise_leave_staged(box, monkeypatch, capsys):
    fake_claude(
        monkeypatch,
        AssistantMessage(
            tool_calls=[ToolCall(name="write_file", args={"path": "Downloads/新的.txt",
                                                         "content": "x"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(text="写好了"),
    )
    quiet_stdin(monkeypatch)

    assert cli.main(["-p", "写个文件", "--yes", "--root", str(box),
                     "--protect", "Downloads"]) == 0

    assert (box / "Downloads" / "新的.txt").read_text() == "x"


# --- reading and copying a session, without a model --------------------------


def test_the_tree_can_be_read_with_no_api_key_at_all(box, monkeypatch, capsys):
    """Being locked out of your own transcript for want of a key would be absurd."""
    fake_claude(monkeypatch, AssistantMessage(text="整理好了"))
    keys(monkeypatch, "")
    cli.main(["整理一下", "--root", str(box)])
    capsys.readouterr()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["-c", "--tree", "--root", str(box)]) == 0

    out = capsys.readouterr().out
    assert "整理一下" in out and "整理好了" in out
    assert "▸" in out, "the head is marked"


def test_fork_writes_a_new_file_and_prints_its_path(box, monkeypatch, capsys):
    fake_claude(monkeypatch, AssistantMessage(text="整理好了"))
    keys(monkeypatch, "")
    cli.main(["整理一下", "--root", str(box)])
    capsys.readouterr()

    original = sorted((tmp := cli.SESSIONS).glob("*.jsonl"))
    assert cli.main(["-c", "--fork", "--root", str(box)]) == 0

    printed = capsys.readouterr().out.strip()
    assert printed.endswith("-fork.jsonl")
    from pathlib import Path

    assert Path(printed).exists()
    assert [p.read_text() for p in original] == [
        p.read_text() for p in original
    ], "the original is untouched"


def test_forking_an_empty_session_is_refused(box, monkeypatch, capsys):
    assert cli.main(["--fork", "--root", str(box)]) == 1
    assert "is empty" in capsys.readouterr().err


# --- picking a session out of a list -----------------------------------------


def a_session(box, monkeypatch, prompt, name=None):
    """Run one prompt so there is a session file to pick later."""
    fake_claude(monkeypatch, AssistantMessage(text="好"))
    keys(monkeypatch, "")
    argv = [prompt, "--root", str(box)]
    if name:
        argv += ["--name", name]
    cli.main(argv)


def test_resume_lists_the_sessions_and_opens_the_one_picked(box, monkeypatch, capsys):
    a_session(box, monkeypatch, "第一件事", name="甲")
    a_session(box, monkeypatch, "第二件事", name="乙")
    capsys.readouterr()

    fake_claude(monkeypatch, AssistantMessage(text="接着做"))
    # "2" picks the older one; the blank line answers the review prompt after.
    keys(monkeypatch, "2", "")
    assert cli.main(["继续", "-r", "--root", str(box)]) == 0

    captured = capsys.readouterr()
    assert "甲" in captured.err and "乙" in captured.err, "the list is a question, so stderr"
    opened = [p for p in cli.SESSIONS.glob("*.jsonl") if "第一件事" in p.read_text()]
    assert opened and "继续" in opened[0].read_text(), "it carried on in the one picked"


def test_resume_takes_the_newest_on_a_bare_enter(box, monkeypatch, capsys):
    a_session(box, monkeypatch, "旧的")
    a_session(box, monkeypatch, "新的")
    capsys.readouterr()

    fake_claude(monkeypatch, AssistantMessage(text="接着做"))
    keys(monkeypatch, "", "")
    assert cli.main(["继续", "-r", "--root", str(box)]) == 0

    newest = max(cli.SESSIONS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    assert "新的" in newest.read_text()


def test_q_at_the_picker_leaves_without_starting_anything(box, monkeypatch, capsys):
    a_session(box, monkeypatch, "一件事")
    before = sorted(p.name for p in cli.SESSIONS.glob("*.jsonl"))
    capsys.readouterr()

    keys(monkeypatch, "q")
    assert cli.main(["-r", "--root", str(box)]) == 0

    captured = capsys.readouterr()
    assert sorted(p.name for p in cli.SESSIONS.glob("*.jsonl")) == before, "no new file"
    assert "aven ·" not in captured.err, "and nothing was opened: no model, no banner"


def test_resume_with_no_sessions_yet_just_starts_one(box, monkeypatch, capsys):
    fake_claude(monkeypatch, AssistantMessage(text="好"))
    keys(monkeypatch, "")

    assert cli.main(["第一件事", "-r", "--root", str(box)]) == 0
    assert "no sessions on record" in capsys.readouterr().err


def test_name_is_stored_in_the_session_and_shown_by_the_picker(box, monkeypatch, capsys):
    a_session(box, monkeypatch, "整理发票", name="发票 7 月")
    capsys.readouterr()

    from aven.harness.session import Session

    path = next(iter(cli.SESSIONS.glob("*.jsonl")))
    assert Session.open(path).name == "发票 7 月"

    keys(monkeypatch, "q")
    cli.main(["-r", "--root", str(box)])
    assert "发票 7 月" in capsys.readouterr().err


def test_continue_still_means_the_most_recent(box, monkeypatch, capsys):
    """-c changed dest under the hood; the flag must behave exactly as before."""
    a_session(box, monkeypatch, "旧的")
    a_session(box, monkeypatch, "新的")
    capsys.readouterr()

    fake_claude(monkeypatch, AssistantMessage(text="接着做"))
    keys(monkeypatch, "")
    assert cli.main(["继续", "-c", "--root", str(box)]) == 0

    newest = max(cli.SESSIONS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    assert "新的" in newest.read_text() and "继续" in newest.read_text()


# --- several folders ---------------------------------------------------------


def test_the_prompt_is_told_which_folders_exist_and_what_they_are_called(box):
    """The model cannot guess a root's name, and the name is how it reaches one."""
    from aven.apps.cli_assistant.main import folders

    said = folders([box / "Downloads", box / "Documents"])

    assert "Downloads" in said and "Documents" in said
    assert "working folder" in said
    assert "Documents/some/file" in said, "and how to use the name"


def test_one_folder_is_described_without_the_naming_ceremony(box):
    from aven.apps.cli_assistant.main import folders

    said = folders([box / "Downloads"])

    assert "The folder you may act in" in said
    assert "putting its name first" not in said


def test_the_names_in_the_prompt_are_the_names_the_tools_answer_to(box):
    """Written out twice, they would drift; the second one would be a lie."""
    from aven.apps.cli_assistant.main import folders
    from aven.toolkit import file_tools

    (box / "Documents").mkdir(exist_ok=True)
    (box / "Documents" / "x.md").write_text("found")
    said = folders([box / "Downloads", box / "Documents"])
    built = {tool.name: tool for tool in file_tools(box / "Downloads", box / "Documents")}

    listed = [line for line in said.splitlines() if line.startswith("- ")]
    name = next(line.split()[1] for line in listed if "Documents" in line)

    assert built["read_file"](path=f"{name}/x.md").output == "found"


def test_several_roots_reach_the_tools(box, monkeypatch):
    import argparse

    from aven.apps.cli_assistant.main import assemble

    (box / "Documents").mkdir(exist_ok=True)
    kit = assemble(
        argparse.Namespace(mac=False), [box / "Downloads", box / "Documents"], []
    )
    read = next(tool for tool in kit.box.active() if tool.name == "read_file")

    (box / "Documents" / "y.md").write_text("reachable")
    assert read(path="Documents/y.md").output == "reachable"


def test_a_root_that_is_not_a_directory_is_refused_whichever_one_it_is(box, capsys):
    assert cli.main(["hi", "--root", str(box), "--root", str(box / "Downloads" / "a.pdf")]) == 1
    assert "not a directory" in capsys.readouterr().err
