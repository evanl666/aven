"""Tests for the aven command, with no key and no network."""

import io
import os
from pathlib import Path
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


# --- finding an API key ------------------------------------------------------


def test_an_exported_key_wins_over_a_stored_one(monkeypatch, tmp_path):
    """Somebody who exported one meant that one. A stored key silently
    overriding it would be the worst kind of surprise - the wrong account
    billed, with nothing on screen to say why."""
    from aven.harness.vault import Locked
    from aven.terminal import app

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: _holding(tmp_path, "from-the-vault"))

    assert app.key_is_available() is True
    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-shell"


def test_a_stored_key_is_found_when_the_shell_has_none(monkeypatch, tmp_path):
    """A window launched from the dock has no shell and therefore no exported
    variable. Telling somebody to edit their login profile so a desktop app can
    start is not an answer."""
    from aven.terminal import app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: _holding(tmp_path, "from-the-vault"))

    assert app.key_is_available() is True
    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-vault", (
        "and it reaches the SDK, which reads the environment"
    )


def test_no_key_anywhere_is_reported_rather_than_guessed_at(monkeypatch, tmp_path):
    from aven.harness.vault import Locked
    from aven.terminal import app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: Locked(tmp_path / "empty.json"))

    assert app.key_is_available() is False


def test_storing_a_key_reads_it_from_stdin_not_from_the_command_line(monkeypatch, tmp_path):
    """Anything on a command line is visible in `ps` to every process on the
    machine, and lands in the shell history besides."""
    from aven.harness.vault import Locked
    from aven.terminal import app

    vault = Locked(tmp_path / "creds.json")
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: vault)
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-ant-pasted\n"))

    assert app.store_key() == 0
    assert vault.get("anthropic") == {"key": "sk-ant-pasted"}


def test_pasting_nothing_stores_nothing(monkeypatch, tmp_path):
    from aven.harness.vault import Locked
    from aven.terminal import app

    vault = Locked(tmp_path / "creds.json")
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: vault)
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))

    assert app.store_key() == 1
    assert vault.get("anthropic") is None


def _holding(tmp_path, key):
    from aven.harness.vault import Locked

    vault = Locked(tmp_path / "creds.json")
    vault.put("anthropic", {"key": key})
    return vault


def test_an_exported_key_is_reported_as_coming_from_the_shell(monkeypatch, tmp_path):
    """Because it wins, and replacing the stored one then changes nothing. A
    Replace button that appears to work and does not is the worst shape a bug
    can take."""
    from aven.terminal import app

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: _holding(tmp_path, "stored"))

    assert app.key_source() == "environment"


def test_a_stored_key_is_reported_as_such_however_often_it_is_asked(monkeypatch, tmp_path):
    """It is put into the environment so the SDK can read it. Asked a second
    time, this must not find its own work and call it an export."""
    from aven.terminal import app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: _holding(tmp_path, "stored"))

    assert app.key_source() == "keychain"
    assert app.key_source() == "keychain", "and again"
    assert os.environ["ANTHROPIC_API_KEY"] == "stored"


def test_storing_a_key_makes_it_the_stored_one_even_over_an_export(monkeypatch, tmp_path):
    """Somebody who just typed a key into the window meant that one."""
    from aven.harness.vault import Locked
    from aven.terminal import app

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: Locked(tmp_path / "c.json"))

    app.keep_key("just-typed")

    assert app.key_source() == "keychain"
    assert os.environ["ANTHROPIC_API_KEY"] == "just-typed"


def test_nothing_anywhere_is_reported_as_nothing(monkeypatch, tmp_path):
    from aven.harness.vault import Locked
    from aven.terminal import app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: Locked(tmp_path / "empty.json"))

    assert app.key_source() == ""


# --- the prompt must describe the tray it actually has -----------------------
#
# Found by a benchmark sweep. --yes fires irreversible calls as they are made,
# but both apps' prompts were written when it committed them at the end, and
# they went on telling the model that a build would be held and would not run.
# A model that believes it cannot compile does not compile - it writes the code
# and tells you how to test it yourself. Four of thirty tasks ended that way.


def prompt_seen(monkeypatch, *replies):
    """Run with a fake model that keeps the system prompt it was handed."""
    seen: dict[str, str] = {}
    queue = list(replies) or [AssistantMessage(text="done")]

    class Capturing:
        def __init__(self, **kw):
            if kw.get("system"):
                seen["system"] = kw["system"]
            self.usage = SimpleNamespace(last_input=0)

        async def context_window(self):
            return 200_000

        async def __call__(self, messages):
            return replace(queue.pop(0) if len(queue) > 1 else queue[0], id=new_id())

    monkeypatch.setattr(foundation, "Claude", Capturing)
    return seen


def test_unattended_runs_are_told_nobody_is_there(box, monkeypatch):
    seen = prompt_seen(monkeypatch)
    quiet_stdin(monkeypatch)

    cli.main(["-p", "做点事", "--yes", "--root", str(box)])

    said = seen["system"]
    assert "Nobody is at this terminal" in said
    assert "Somebody is at this terminal" not in said, "both halves cannot be true"
    assert "staged" in said, "it still has to say what staged would have meant"


def test_attended_runs_are_told_somebody_is_there(box, monkeypatch):
    seen = prompt_seen(monkeypatch)
    keys(monkeypatch, "d")

    cli.main(["-p", "做点事", "--root", str(box)])

    said = seen["system"]
    assert "Somebody is at this terminal" in said
    assert "Nobody is at this terminal" not in said
    assert "HAS NOT HAPPENED" in said


def test_the_prompt_never_promises_staging_that_will_not_happen(box, monkeypatch):
    """The one that ties the words to the behaviour.

    If the prompt says nothing will come back staged, then an irreversible call
    in that same run must not come back staged. This is the invariant the bug
    broke, and the only test here that would have caught it.
    """
    seen = prompt_seen(
        monkeypatch,
        AssistantMessage(
            tool_calls=[ToolCall(name="write_file",
                                 args={"path": "Downloads/新的.txt", "content": "x"})],
            stop_reason="tool_use",
        ),
        AssistantMessage(text="写好了"),
    )
    quiet_stdin(monkeypatch)

    cli.main(["-p", "写个文件", "--yes", "--root", str(box), "--protect", "Downloads"])

    promised_nothing_waits = 'nothing will come back "staged"' in seen["system"]
    nothing_waited = (box / "Downloads" / "新的.txt").exists()

    assert promised_nothing_waits, "the unattended prompt no longer makes the promise"
    assert nothing_waited, "it promised the call would happen, and it did not"


def test_a_coding_run_is_told_the_shell_is_not_sandboxed_to_the_folder(box, monkeypatch):
    """download-youtube gave up in one request: "My tools only allow me to read,
    write, and manage files within this local project folder." It had a shell."""
    import aven.apps.cli_code.main as code

    monkeypatch.setattr(code, "SESSIONS", box / "code-sessions")
    seen = prompt_seen(monkeypatch)
    quiet_stdin(monkeypatch)

    code.main(["-p", "do something", "--yes", "--root", str(box)])

    # Whitespace-normalised: the prompt is wrapped prose, so a sentence to
    # assert on spans lines and a literal match would depend on where.
    said = " ".join(seen["system"].split())
    assert "real shell" in said
    assert "not its limit" in said, "the folder limit has to be scoped to the file tools"
    assert "how they could check it for themselves is not finishing the job" in said


def test_a_context_that_cannot_be_shortened_ends_with_a_sentence(box, monkeypatch, capsys):
    """It used to end with aven.harness.calling.ContextOverflow on the screen.

    The loop compacts and asks again; reaching the top means that did not get
    it under the limit, so there is nothing to retry - and the provider's own
    words, "prompt is too long: 200155 tokens > 200000 maximum", are not
    something a person can act on.
    """
    from aven.harness.calling import ContextOverflow

    class TooLong:
        def __init__(self, **_):
            self.usage = SimpleNamespace(last_input=0)

        async def context_window(self):
            return 200_000

        async def __call__(self, messages):
            raise ContextOverflow("prompt is too long: 200155 tokens > 200000 maximum")

    monkeypatch.setattr(foundation, "Claude", TooLong)
    quiet_stdin(monkeypatch)

    assert cli.main(["-p", "做点事", "--yes", "--root", str(box)]) == 1

    said = capsys.readouterr().err
    assert "ContextOverflow" not in said
    assert "Traceback" not in said
    assert "fresh session" in said, "it has to say what to do instead"
    assert "200155" in said, "and keep the number for whoever wants it"


# --- stdin that nobody will ever write to ------------------------------------
#
# `sys.stdin.read()` waits for EOF, and an unattended run - a cron line, a CI
# step, a harness that backgrounded the process - routinely inherits a pipe
# that stays open forever. aven simply stopped: no output, no timeout, no clue.
# It cost fifty-three minutes of a benchmark run to notice, and what was
# finally diagnostic was that the session file had never been written at all.


def answers_with_stdin_held_open(prompt, wait=8.0):
    """Run `piped(prompt)` in a real process, with the write end held open.

    A subprocess, because the bug needs a real pipe: a StringIO cannot block
    and Popen.communicate() closes stdin as part of its job, which is how the
    first version of this test passed against the bug.
    """
    import subprocess
    import sys as _sys
    import time

    root = Path(__file__).resolve().parent.parent
    snippet = (
        f"import sys; sys.path.insert(0, {str(root)!r});"
        "from aven.terminal.app import piped;"
        "sys.stdout.write(piped(sys.argv[1] or None) or '<empty>');"
        "sys.stdout.flush()"
    )
    going = subprocess.Popen(
        [_sys.executable, "-c", snippet, prompt],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(root),
    )
    began = time.time()
    try:
        while time.time() - began < wait:
            if going.poll() is not None:
                return going.stdout.read().decode()
            time.sleep(0.1)
        return None  # never answered
    finally:
        going.kill()
        going.stdin.close()


def test_a_pipe_nobody_writes_to_does_not_stop_a_run_that_has_its_prompt():
    said = answers_with_stdin_held_open("sort out the downloads")

    assert said is not None, (
        "it is still waiting on a pipe that will never be written to - the "
        "failure mode is a cron job that does nothing and says nothing"
    )
    assert said == "sort out the downloads"


def test_a_pipe_is_still_waited_for_when_it_is_the_whole_request():
    """`cat diff | aven` must wait: there is no prompt, so stdin is the request
    and giving up on it would be giving up on the input."""
    assert answers_with_stdin_held_open("", wait=3.0) is None


def test_material_piped_in_still_arrives_before_the_prompt(monkeypatch):
    """The documented behaviour, which the fix must not cost."""
    import io

    from aven.terminal.app import piped

    monkeypatch.setattr("sys.stdin", io.StringIO("diff --git a/x b/x\n+一行"))
    said = piped("review this change")

    assert said.startswith("diff --git")
    assert said.endswith("review this change")
