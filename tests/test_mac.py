"""Tests for the macOS actuators, without touching Mail or Calendar."""

import subprocess

import pytest

from aven.apps.cli_assistant import mac
from aven.apps.cli_assistant.mac import OsaError, mac_tools, osa

HOSTILE = 'x" & (do shell script "rm -rf ~") & "'


class Shell(list):
    """Every command aven shelled out, plus the stdout to hand back."""

    def __init__(self):
        super().__init__()
        self.replies: list[str] = []


@pytest.fixture
def calls(monkeypatch):
    recorded = Shell()

    def fake_run(command, timeout):
        recorded.append(command)
        return recorded.replies.pop(0) if recorded.replies else ""

    monkeypatch.setattr(mac, "_run", fake_run)
    return recorded


@pytest.fixture
def tools():
    return {t.name: t for t in mac_tools()}


def test_arguments_go_after_the_separator_never_into_the_script(calls):
    osa("on run argv\nreturn 1\nend run", "a", 7)

    command = calls[0]
    assert command[:2] == ["osascript", "-e"]
    assert command[3:] == ["--", "a", "7"], "values live past --, not in the script"


def test_a_hostile_value_stays_a_value(calls, tools):
    """The injection this module exists to prevent."""
    tools["send_mail"](to="boss@corp.com", subject=HOSTILE, body="hi")

    script, args = calls[0][2], calls[0][4:]
    assert HOSTILE not in script, "the payload must never reach the script text"
    assert HOSTILE in args


def test_a_value_starting_with_a_dash_is_still_a_value(calls):
    osa("on run argv\nend run", "--help")
    assert calls[0][-1] == "--help"


def test_create_event_parses_the_date_in_python(calls, tools):
    calls.replies.append("UID-123")
    result = tools["create_event"](
        calendar="工作", title="牙医", start="2026-09-21 14:30", minutes=30
    )

    assert calls[0][3:] == ["--", "工作", "牙医", "2026", "9", "21", "14", "30", "30"]
    assert "2026-09-21 14:30" in result.output


def test_a_bad_date_fails_before_anything_is_created(calls, tools):
    with pytest.raises(ValueError):
        tools["create_event"](calendar="工作", title="x", start="下周二")
    assert calls == [], "nothing was asked of Calendar"


def test_create_event_undo_deletes_by_uid(calls, tools):
    calls.replies.append("UID-123")
    result = tools["create_event"](calendar="工作", title="牙医", start="2026-09-21 14:30")

    result.undo()

    assert calls[1][3:] == ["--", "工作", "UID-123"]
    assert "delete" in calls[1][2]


def test_draft_undo_deletes_that_draft(calls, tools):
    calls.replies.append("4242")
    result = tools["draft_mail"](to="a@b.c", subject="hi", body="there")

    result.undo()

    assert calls[1][3:] == ["--", "4242"]


def test_send_mail_is_irreversible_and_offers_no_undo(calls, tools):
    assert tools["send_mail"].risk == "irreversible"
    assert tools["send_mail"](to="a@b.c", subject="hi").undo is None


def test_drafting_is_reversible_so_it_needs_no_approval(tools):
    """Draft then send is the shape: the reversible half runs, the send waits."""
    assert tools["draft_mail"].risk == "reversible"
    assert tools["list_events"].risk == "read"


def test_spotlight_caps_the_output_and_says_so(calls, tools):
    calls.replies.append("\n".join(f"/f{i}" for i in range(50)))
    output = tools["spotlight"](query="invoice", limit=3).output

    assert output.count("\n") == 3
    assert "and 47 more" in output


def test_a_timeout_becomes_a_readable_error(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=60)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(OsaError, match="longer than"):
        osa("on run argv\nend run")


def test_a_refusal_surfaces_what_the_app_said(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="Not authorised to send Apple events"
        ),
    )
    with pytest.raises(OsaError, match="Not authorised"):
        osa("on run argv\nend run")
