"""Tests for decisions already made.

Almost all of these are written from the attacking side. A standing approval is
the one feature here that lets something happen without being asked, so the
question is never "does it work" but "what can get through it" - a wildcard, an
empty fragment, a tool that said no, a malformed file, a preview that shifted.

The failure this exists to prevent is subtle: an assistant that asks about the
fortieth identical filing move teaches people to approve without reading, and
after that the tray protects nothing. So the feature is necessary. It is also
exactly the shape of the thing that gets abused, which is why it is this narrow.
"""

import pytest

from aven.harness.tools import ToolResult, tool
from aven.harness.tx import Approval, Standing, Tray
from aven.harness.tx import standing as module

ran: list[str] = []


@pytest.fixture(autouse=True)
def clean():
    ran.clear()
    yield


@tool(risk="irreversible", preview="file {what} into 报销/")
def file_away(what: str) -> ToolResult:
    """File something."""
    ran.append(what)
    return ToolResult(output=f"filed {what}")


@tool(risk="irreversible", preview="send mail to {to}", pre_approvable=False)
def send_mail(to: str) -> ToolResult:
    """Send mail."""
    ran.append(f"mail:{to}")
    return ToolResult(output="sent")


def granting(*approvals: Approval) -> Standing:
    return Standing(list(approvals))


# --- what it is for ----------------------------------------------------------


def test_without_an_approval_an_irreversible_call_waits():
    """The behaviour before any of this existed, and still the default."""
    tray = Tray()

    output, staged = tray.execute(file_away, {"what": "a.pdf"})

    assert staged is True
    assert ran == []
    assert "waiting" in output


def test_an_approval_lets_the_call_through():
    tray = Tray(standing=granting(Approval(tool="file_away", when="报销/")))

    output, staged = tray.execute(file_away, {"what": "a.pdf"})

    assert staged is False
    assert ran == ["a.pdf"]
    assert output == "filed a.pdf"


def test_a_pre_approved_call_says_so_in_the_transcript():
    """Not implied. A record has to show plainly it was not a fresh decision."""
    tray = Tray(standing=granting(Approval(tool="file_away", when="报销/")))

    tray.execute(file_away, {"what": "a.pdf"})

    entry = tray.entries[0]
    assert entry.state == "committed"
    assert entry.approved_by is not None
    assert "file_away" in entry.approved_by and "报销/" in entry.approved_by


def test_a_pre_approved_call_is_still_undoable_if_it_can_be():
    """Approved is not the same as unrecorded."""
    undone = []

    @tool(risk="irreversible", preview="move {what}")
    def movable(what: str) -> ToolResult:
        """Move."""
        return ToolResult(output="moved", undo=lambda: undone.append(what))

    tray = Tray(standing=granting(Approval(tool="movable", when="move")))
    tray.execute(movable, {"what": "x"})

    tray.undo()

    assert undone == ["x"]


# --- what cannot get through -------------------------------------------------


def test_a_tool_that_refuses_cannot_be_pre_approved_by_any_file():
    """The mail they would be approving is one they have not read."""
    tray = Tray(standing=granting(Approval(tool="send_mail", when="send mail")))

    _, staged = tray.execute(send_mail, {"to": "a@b.c"})

    assert staged is True
    assert ran == []


def test_an_approval_for_another_tool_does_not_carry_over():
    tray = Tray(standing=granting(Approval(tool="something_else", when="报销/")))

    _, staged = tray.execute(file_away, {"what": "a.pdf"})

    assert staged is True


def test_a_fragment_that_is_not_in_the_preview_does_not_match():
    tray = Tray(standing=granting(Approval(tool="file_away", when="发票/")))

    _, staged = tray.execute(file_away, {"what": "a.pdf"})

    assert staged is True, "they agreed to 发票/, and this says 报销/"


def test_the_match_is_against_the_preview_the_person_read():
    """Not the arguments. The sentence they agreed to is the sentence to check."""
    approval = Approval(tool="file_away", when="报销/")

    assert approval.covers(file_away, "file a.pdf into 报销/") is True
    assert approval.covers(file_away, "file a.pdf into 别的/") is False


@tool(risk="reversible", preview="change {path}")
def change(path: str) -> ToolResult:
    """Change something."""
    ran.append(path)
    return ToolResult(output="changed", undo=lambda: None)


def test_a_call_the_policy_raised_is_never_pre_approved():
    """The warning is this person's own rule saying "this one is different", and
    an approval written before there was a warning cannot have accounted for it.

    Found by a test that expected the opposite: the warning is appended, so the
    plain wording stays a prefix of the warned one and a substring match honoured
    the old approval silently. The rule is now the blunt one - raised means asked.
    """
    from aven.harness.tx import guard, protect

    tray = Tray(
        policy=guard(protect("合同")),
        standing=granting(Approval(tool="change", when="change 合同/租房.pdf")),
    )

    _, staged = tray.execute(change, {"path": "合同/租房.pdf"})

    assert staged is True
    assert ran == []
    assert "⚠" in tray.pending()[0].preview, "the rule raised it and said why"


def test_not_even_an_approval_written_for_the_warning_itself():
    """Blunt on purpose. "Yes even when warned" is a sentence with no safe
    reading, so there is no way to write it."""
    from aven.harness.tx import guard, protect

    tray = Tray(
        policy=guard(protect("合同")),
        standing=granting(Approval(tool="change", when="⚠")),
    )

    _, staged = tray.execute(change, {"path": "合同/租房.pdf"})

    assert staged is True
    assert ran == []


def test_an_unraised_call_of_the_same_tool_is_still_covered():
    """The rule is about the warning, not about the tool being suspect."""
    from aven.harness.tx import guard, protect

    tray = Tray(
        policy=guard(protect("合同")),
        standing=granting(Approval(tool="change", when="change")),
    )

    _, staged = tray.execute(change, {"path": "notes/a.md"})

    assert staged is False, "nothing raised it, so the decision holds"
    assert ran == ["notes/a.md"]


# --- the file ----------------------------------------------------------------


def test_no_file_grants_nothing(tmp_path):
    assert len(module.read(tmp_path / "nothing.toml")) == 0


def test_a_file_is_read_as_a_list_of_decisions(tmp_path):
    path = tmp_path / "approvals.toml"
    path.write_text(
        '[[approve]]\ntool = "file_away"\nwhen = "报销/"\nnote = "every month"\n',
        encoding="utf-8",
    )

    loaded = module.read(path)

    assert len(loaded) == 1
    assert loaded.approvals[0].tool == "file_away"
    assert loaded.approvals[0].note == "every month"


def test_a_row_with_no_tool_is_not_a_decision_anybody_made(tmp_path):
    """A missing tool means "anything", which nobody writes on purpose."""
    path = tmp_path / "approvals.toml"
    path.write_text('[[approve]]\nwhen = "报销/"\n', encoding="utf-8")

    assert len(module.read(path)) == 0


def test_a_row_with_no_fragment_is_not_one_either(tmp_path):
    """A missing fragment means "always", which is the same thing said quietly."""
    path = tmp_path / "approvals.toml"
    path.write_text('[[approve]]\ntool = "file_away"\n', encoding="utf-8")

    assert len(module.read(path)) == 0


def test_an_empty_fragment_is_refused_as_well(tmp_path):
    path = tmp_path / "approvals.toml"
    path.write_text('[[approve]]\ntool = "file_away"\nwhen = "  "\n', encoding="utf-8")

    assert len(module.read(path)) == 0


def test_a_broken_file_grants_nothing_rather_than_stopping_the_run(tmp_path):
    """A list of conveniences with a typo in it should not refuse to start - and
    granting nothing is the safe direction to fail in."""
    path = tmp_path / "approvals.toml"
    path.write_text("[[approve]\ntool = oops", encoding="utf-8")

    assert len(module.read(path)) == 0


def test_the_path_is_resolved_per_call_so_a_test_can_redirect_it(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))

    assert module.file_for() == tmp_path / ".aven" / "approvals.toml"


def test_the_first_matching_decision_wins(tmp_path):
    """Their own list in their own order; a most-specific rule would mean it no
    longer reads the way it behaves."""
    loaded = granting(
        Approval(tool="file_away", when="报销/", note="first"),
        Approval(tool="file_away", when="a.pdf", note="second"),
    )

    assert loaded.covering(file_away, "file a.pdf into 报销/").note == "first"


# --- committing some of a batch ----------------------------------------------


def test_only_the_named_entries_fire():
    """A batch with a purchase in it should not have to be accepted whole."""
    tray = Tray()
    tray.execute(file_away, {"what": "a.pdf"})
    tray.execute(file_away, {"what": "b.pdf"})
    keep = tray.pending()[1].id

    done = tray.commit(only=[keep])

    assert ran == ["b.pdf"]
    assert len(done) == 1
    assert len(tray.pending()) == 1, "the other one is still waiting"


def test_committing_nothing_named_fires_nothing():
    tray = Tray()
    tray.execute(file_away, {"what": "a.pdf"})

    assert tray.commit(only=[]) == []
    assert ran == []
    assert len(tray.pending()) == 1


def test_committing_everything_is_still_the_default():
    tray = Tray()
    tray.execute(file_away, {"what": "a.pdf"})
    tray.execute(file_away, {"what": "b.pdf"})

    tray.commit()

    assert ran == ["a.pdf", "b.pdf"]
