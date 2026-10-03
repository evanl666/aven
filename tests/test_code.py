"""Tests for the coding app.

Most of these are about one question: whether a command changes anything. Every
True out of `read_only` is a promise that the command runs without anybody being
asked, so these tests are the promise, and they are written from the direction
that matters - not "does `ls` run" but "does anything that writes slip through".
"""

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from aven.apps.cli_code.main import assemble, blueprint
from aven.apps.cli_code.tools import Outside, code_tools, read_only
from aven.harness.tx import Tray, Verdict, guard


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / "src" / "b.py").write_text("from a import hello\nhello()\n")
    (tmp_path / "notes.md").write_text("nothing to see\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("hello()\n")
    return tmp_path


def tools(project, **kwargs):
    return {tool.name: tool for tool in code_tools(project, **kwargs)}


# --- what counts as reading --------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la src",
        "cat src/a.py",
        "grep -i hello src/a.py",
        "rg --json hello",
        "wc -l src/*.py",
        "find . -name '*.py'",
        "git status",
        "git log --oneline -20",
        "git diff HEAD~1",
        "git -c color.ui=false status",
        "cat src/a.py | head -5",
        "ls && git status",
        "which python",
    ],
)
def test_a_reading_command_runs_without_asking(command):
    assert read_only(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf src",
        "mv src old",
        "git commit -m x",
        "git push",
        "git checkout main",
        "npm install",
        "pytest",
        "python setup.py install",
        "./configure",
        "/bin/ls",
        "sh -c 'rm x'",
        "bash script.sh",
        "sed -i s/a/b/ src/a.py",
        "awk '{print > \"out\"}' src/a.py",
        "ls > listing.txt",
        "cat src/a.py >> other.py",
        "ls 2> errors.txt",
        "echo `rm -rf src`",
        "echo $(rm -rf src)",
        "ls; rm -rf src",
        "ls && rm -rf src",
        "cat src/a.py | xargs rm",
        "find . -name '*.py' -delete",
        "find . -exec rm {} ;",
        "git status; git push",
        "FOO=1 rm x",
        "ls 'unbalanced",
        "",
        "   ",
    ],
)
def test_anything_that_could_write_waits(command):
    assert read_only(command) is False


def test_every_piece_of_a_chain_has_to_read():
    """The first word being safe is what a shallow check would look at."""
    assert read_only("ls") is True
    assert read_only("ls | rm") is False
    assert read_only("git log | git push") is False


def test_a_project_can_vouch_for_its_own_command():
    assert read_only("pytest -q") is False
    assert read_only("pytest -q", frozenset({"pytest"})) is True
    assert read_only("pytest -q && rm x", frozenset({"pytest"})) is False, "only that one"


# --- how that reaches the tray -----------------------------------------------


def test_a_reading_command_is_read_risk_and_a_writing_one_is_not(project):
    run = tools(project)["run_command"]

    assert run.risk_for({"command": "git status"}) == "read"
    assert run.risk_for({"command": "rm -rf src"}) == "irreversible"


def test_a_reading_command_actually_runs(project):
    tray = Tray()

    output, staged = tray.execute(tools(project)["run_command"], {"command": "ls"})

    assert staged is False
    assert "notes.md" in output


def test_a_writing_command_is_staged_and_does_not_run(project):
    tray = Tray()

    _, staged = tray.execute(
        tools(project)["run_command"], {"command": "rm -rf src"}
    )

    assert staged is True
    assert (project / "src").exists(), "it really did not run"
    assert tray.pending()[0].risk == "irreversible"


def test_committing_a_staged_command_is_what_finally_runs_it(project):
    tray = Tray()
    tray.execute(tools(project)["run_command"], {"command": "rm -rf src"})

    tray.commit()

    assert not (project / "src").exists()


def test_a_policy_can_still_raise_a_command_the_tool_called_reading(project):
    """The tool decides its own risk; the policy may only tighten it.

    Not with `protect`, which leaves reads alone on purpose - knowing a file is
    there is not changing it. With a rule that does look at reads, to show the
    raising still works when the risk came from the arguments rather than from a
    declaration.
    """
    fussy = lambda tool, args, done: Verdict(
        risk="irreversible", reason="every shell command, please"
    ) if tool.name == "run_command" else None
    tray = Tray(policy=guard(fussy))

    _, staged = tray.execute(tools(project)["run_command"], {"command": "ls"})

    assert staged is True, "read by the tool, raised by the person's rule"


def test_a_failing_command_reports_its_exit_code(project):
    """A test run that failed is the most useful fact in the reply."""
    output = tools(project)["run_command"](command="ls nope-does-not-exist").output

    assert "[exited" in output


def test_a_command_that_says_nothing_says_so(project):
    assert "(no output)" in tools(project)["run_command"](command="true").output


# --- searching ---------------------------------------------------------------


def test_grep_reports_the_file_and_line_of_each_match(project):
    found = tools(project)["grep"](pattern="hello").output

    assert "src/a.py:1:" in found
    assert "src/b.py:1:" in found


def test_grep_skips_what_nobody_meant_to_search(project):
    """node_modules and .git make a search useless if they are in it."""
    assert "node_modules" not in tools(project)["grep"](pattern="hello").output


def test_grep_can_be_narrowed_to_a_kind_of_file(project):
    found = tools(project)["grep"](pattern="nothing", glob="*.py").output

    assert "no match" in found, "notes.md is not a .py"


def test_a_broken_pattern_is_reported_rather_than_raised(project):
    assert "not a valid regular expression" in tools(project)["grep"](pattern="(").output


def test_grep_cannot_read_outside_the_project(project):
    with pytest.raises(Outside):
        tools(project)["grep"](pattern="x", path="../..")


def test_glob_lists_matches_newest_first(project):
    import os
    import time

    os.utime(project / "src" / "b.py", (time.time() + 10,) * 2)

    listed = tools(project)["glob"](pattern="src/*.py").output.splitlines()

    assert listed == ["src/b.py", "src/a.py"]


def test_glob_says_so_when_nothing_matches(project):
    assert "nothing matches" in tools(project)["glob"](pattern="*.rs").output


def test_searching_needs_nobody_s_approval(project):
    """The whole reason to prefer them over shelling out."""
    built = tools(project)
    assert built["grep"].risk == "read"
    assert built["glob"].risk == "read"


# --- the app -----------------------------------------------------------------


def test_the_coding_app_holds_nothing_back(project):
    """It reaches for all of these in the first turn of almost every task."""
    kit = assemble(argparse.Namespace(allow=[]), [project], [])

    names = {tool.name for tool in kit.box.active()}

    assert {"run_command", "grep", "glob"} <= names
    assert {"read_file", "write_file", "edit_file", "list_dir"} <= names
    assert kit.box.dormant() == [], "nothing waiting, so no use_tools either"


def test_allow_reaches_the_shell(project):
    kit = assemble(argparse.Namespace(allow=["pytest"]), [project], [])
    run = next(tool for tool in kit.box.active() if tool.name == "run_command")

    assert run.risk_for({"command": "pytest -q"}) == "read"


def test_the_coding_app_keeps_its_sessions_apart():
    """`-c` here must never land in a conversation about somebody's calendar."""
    from aven.apps.cli_assistant.main import blueprint as assistant

    assert blueprint().sessions != assistant().sessions


def test_both_apps_are_the_same_foundation():
    assistant = __import__(
        "aven.apps.cli_assistant.main", fromlist=["blueprint"]
    ).blueprint()

    assert type(blueprint()) is type(assistant)
    assert blueprint().program == "aven-code"
    assert assistant.program == "aven"


# --- big results do not stay in the conversation -----------------------------
#
# The assistant has had this since it was built; aven-code did not, which is
# backwards - `cat` on a generated file and a build log are both ordinary here
# and both enormous. A benchmark task died on "prompt is too long: 200155
# tokens > 200000", over by 155, with the fix sitting in the other app.


def test_a_big_result_is_kept_out_of_the_conversation(tmp_path, monkeypatch):
    import aven.apps.cli_code.main as code
    from aven.toolkit.offload import BIG

    monkeypatch.setattr(code, "HOME", tmp_path / "home")
    kit = code.assemble(
        SimpleNamespace(allow=[]), [tmp_path], []
    )

    assert kit.offload is not None, "aven-code is carrying everything again"

    huge = "x" * (BIG * 3)
    stood_in = kit.offload("call-1", "run_command", huge)

    assert len(stood_in) < len(huge) / 4
    assert "kept out of the conversation" in stood_in
    assert "recall(" in stood_in, "and how to read it back"


def test_the_tool_to_read_it_back_is_there_from_the_first_turn(tmp_path, monkeypatch):
    """A result can be offloaded on turn one; recall cannot be in a group."""
    import aven.apps.cli_code.main as code

    monkeypatch.setattr(code, "HOME", tmp_path / "home")
    kit = code.assemble(SimpleNamespace(allow=[]), [tmp_path], [])

    assert "recall" in {t.name for t in kit.box.active()}


def test_a_small_result_is_left_alone(tmp_path, monkeypatch):
    """Below the threshold the round trip costs more than carrying it."""
    import aven.apps.cli_code.main as code

    monkeypatch.setattr(code, "HOME", tmp_path / "home")
    kit = code.assemble(SimpleNamespace(allow=[]), [tmp_path], [])

    assert kit.offload("call-1", "list_dir", "three\nshort\nlines") == "three\nshort\nlines"
