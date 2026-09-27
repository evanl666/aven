"""Tests for judging a call by its arguments."""

from aven.harness.tools import ToolResult, tool
from aven.harness.tx import Tray, Verdict, bulk, guard, protect


@tool(risk="read", preview="看 {path}")
def look(path: str) -> str:
    """Read something."""
    return "内容"


@tool(risk="reversible", preview="改 {path}")
def change(path: str) -> ToolResult:
    """Change something."""
    return ToolResult(output="changed", undo=lambda: None)


@tool(risk="irreversible", preview="发给 {to}")
def send(to: str) -> str:
    """Send something."""
    return "sent"


def always(risk):
    return lambda tool, args, done: Verdict(risk=risk, reason="因为我说了算")


# --- what a policy may and may not do ----------------------------------------


def test_a_policy_can_raise_a_call_above_what_the_tool_declared():
    verdict = guard(always("irreversible")).judge(change, {"path": "a"}, done=[])

    assert verdict is not None and verdict.risk == "irreversible"


def test_a_policy_cannot_lower_one():
    """The tool knows something the policy does not, and the point of the
    declaration is that it cannot be talked out of it."""
    assert guard(always("read")).judge(send, {"to": "a"}, done=[]) is None
    assert guard(always("reversible")).judge(send, {"to": "a"}, done=[]) is None


def test_a_verdict_equal_to_the_declaration_changes_nothing():
    assert guard(always("reversible")).judge(change, {"path": "a"}, done=[]) is None


def test_the_worst_of_several_verdicts_is_the_one_returned():
    """A call can be both bulk and sensitive; the person hears the worse reason."""
    mild = lambda t, a, d: Verdict(risk="reversible", reason="小事")
    severe = lambda t, a, d: Verdict(risk="irreversible", reason="大事")

    verdict = guard(mild, severe).judge(look, {"path": "a"}, done=[])

    assert verdict.reason == "大事"


def test_rules_that_abstain_are_simply_skipped():
    assert guard(lambda t, a, d: None).judge(change, {"path": "a"}, done=[]) is None


# --- the rules that ship -----------------------------------------------------


def test_bulk_asks_once_the_run_has_changed_enough_things():
    rule = guard(bulk(limit=3))

    assert rule.judge(change, {"path": "a"}, done=[1, 2]) is None
    verdict = rule.judge(change, {"path": "a"}, done=[1, 2, 3])
    assert verdict.risk == "irreversible"
    assert "3 处" in verdict.reason


def test_bulk_leaves_reading_alone_however_much_of_it_there_is():
    assert guard(bulk(limit=1)).judge(look, {"path": "a"}, done=[1, 2, 3]) is None


def test_protect_matches_the_path_as_the_model_wrote_it():
    rule = guard(protect(".ssh", "合同"))

    assert rule.judge(change, {"path": "合同/租房.pdf"}, done=[]).risk == "irreversible"
    assert rule.judge(change, {"path": "notes/a.md"}, done=[]) is None


def test_protect_leaves_reading_alone():
    """Knowing a file is there is not the same as changing it."""
    assert guard(protect("合同")).judge(look, {"path": "合同/租房.pdf"}, done=[]) is None


# --- what the tray does with it ----------------------------------------------


def test_a_raised_call_waits_instead_of_running():
    done = []

    @tool(risk="reversible", preview="改 {path}")
    def touched(path: str) -> ToolResult:
        """Change something."""
        done.append(path)
        return ToolResult(output="changed", undo=lambda: None)

    tray = Tray(policy=guard(protect("合同")))

    output, staged = tray.execute(touched, {"path": "合同/租房.pdf"})

    assert staged is True
    assert done == [], "it did not run"
    assert tray.pending()[0].risk == "irreversible"


def test_the_reason_is_carried_into_what_the_person_reads():
    tray = Tray(policy=guard(protect("合同")))
    tray.execute(change, {"path": "合同/租房.pdf"})

    assert "⚠" in tray.pending()[0].preview
    assert "合同" in tray.pending()[0].preview


def test_without_a_policy_nothing_changes():
    tray = Tray()
    output, staged = tray.execute(change, {"path": "合同/租房.pdf"})

    assert staged is False
    assert tray.undoable()[0].risk == "reversible"


def test_bulk_counts_what_the_tray_has_already_recorded():
    """The rule is given the run's own history, which is where 'the fortieth
    move in a row' lives."""
    tray = Tray(policy=guard(bulk(limit=2)))

    tray.execute(change, {"path": "a"})
    tray.execute(change, {"path": "b"})
    _, staged = tray.execute(change, {"path": "c"})

    assert staged is True, "the third one waits"
    assert len(tray.undoable()) == 2
