"""Tests for the richer half of a preview.

A line is enough to recognise a call and not enough to decide on one. "edit
AVEN.md: 财务邮箱是…" tells you almost nothing; the two lines going in tell you
everything. Money is the case a line is least adequate for, which is why Order
exists before anything spends anything.

The terminal is the poorest surface these will ever be drawn on, so drawing them
here is what makes the shapes verified rather than a guess at what a window will
want.
"""

import json

import pytest

from aven.harness.tools import Body, Diff, Moves, Order, ToolResult, as_dict, tool
from aven.harness.tx import Tray
from aven.terminal.render import draw


# --- the tool protocol -------------------------------------------------------


def test_a_tool_with_nothing_more_to_say_says_nothing():
    @tool(risk="read")
    def look() -> str:
        """Look."""
        return "ok"

    assert look.detail_for({}) is None


def test_a_detail_is_built_from_the_arguments():
    @tool(risk="reversible", preview="x",
          detail=lambda old, new, **_: Diff(before=old, after=new))
    def change(old: str, new: str) -> ToolResult:
        """Change."""
        return ToolResult(output="done")

    assert change.detail_for({"old": "a", "new": "b"}) == Diff(before="a", after="b")


def test_a_detail_that_cannot_be_built_does_not_break_the_call():
    """It is drawn before anything runs, including for a path about to be refused.

    A courtesy that can take the run down is not a courtesy.
    """
    @tool(risk="reversible", preview="x", detail=lambda **_: 1 / 0)
    def change(path: str) -> ToolResult:
        """Change."""
        return ToolResult(output="done")

    assert change.detail_for({"path": "a"}) is None
    assert change(path="a").output == "done"


# --- through the tray --------------------------------------------------------


@tool(risk="irreversible", preview="buy {what}",
      detail=lambda what, **_: Order(
          items=[(what, "¥38.00")], total="¥38.00", where="Amazon",
          account="evan@example.com", arrives="Monday"),
      pre_approvable=False)
def buy(what: str) -> ToolResult:
    """Buy something."""
    return ToolResult(output="bought")


def test_a_staged_call_carries_its_detail_into_the_tray():
    """This is what the approval surface reads. Without it, a purchase is a line."""
    tray = Tray()

    tray.execute(buy, {"what": "a kettle"})

    entry = tray.pending()[0]
    assert isinstance(entry.detail, Order)
    assert entry.detail.total == "¥38.00"


def test_a_tool_can_refuse_to_be_pre_approved():
    assert buy.pre_approvable is False


def test_a_tool_is_pre_approvable_unless_it_says_otherwise():
    @tool(risk="reversible", preview="x")
    def ordinary() -> ToolResult:
        """Ordinary."""
        return ToolResult(output="ok")

    assert ordinary.pre_approvable is True


# --- drawn in a terminal -----------------------------------------------------


def test_a_diff_is_drawn_as_the_lines_that_changed():
    """Not both sides: for a whole-file write they are almost the same text."""
    lines = draw(Diff(path="AVEN.md", before="a\nb\nc\n", after="a\nB\nc\n"))

    assert any(line.startswith("-b") for line in lines)
    assert any(line.startswith("+B") for line in lines)
    assert not any("---" in line for line in lines), "the path is in the preview already"


def test_an_order_is_drawn_with_its_total_and_where_it_is_from():
    lines = draw(
        Order(items=[("kettle", "¥38"), ("mug", "¥12")], total="¥50",
              where="Amazon", account="evan@example.com", arrives="Monday")
    )
    shown = "\n".join(lines)

    assert "kettle" in shown and "¥38" in shown
    assert "total" in shown and "¥50" in shown
    assert "Amazon" in shown and "Monday" in shown


def test_a_body_leads_with_its_title():
    lines = draw(Body(title="Invoices → finance@corp.com", text="Attached.\nThanks."))

    assert lines[0].startswith("Invoices")
    assert "Attached." in lines


def test_moves_are_drawn_as_pairs():
    lines = draw(Moves(pairs=[("a.pdf", "paid/a.pdf"), ("b.pdf", "paid/b.pdf")]))

    assert len(lines) == 2
    assert "a.pdf" in lines[0] and "paid/a.pdf" in lines[0]


def test_nothing_is_drawn_for_nothing():
    assert draw(None) == []


def test_a_long_detail_is_clipped_so_the_shape_survives():
    """A transcript does not scroll, and forty lines between two calls loses it."""
    lines = draw(Moves(pairs=[(f"{n}.pdf", f"paid/{n}.pdf") for n in range(40)]))

    assert len(lines) < 20
    assert "more lines" in lines[-1]


# --- across a process boundary -----------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        Diff(path="a", before="x", after="y"),
        Body(title="t", text="b"),
        Moves(pairs=[("a", "b")]),
        Order(items=[("x", "¥1")], total="¥1", where="w", account="a", arrives="t"),
    ],
)
def test_every_shape_survives_json(detail):
    """A window may be another process; the shapes have to cross."""
    record = as_dict(detail)

    assert record["kind"]
    assert json.loads(json.dumps(record, ensure_ascii=False)) == record


def test_nothing_crosses_as_nothing():
    assert as_dict(None) is None


def test_a_new_shape_fails_here_rather_than_in_a_caller_s_parser():
    with pytest.raises(TypeError):
        as_dict("not a detail")


# --- the surface a person actually decides on --------------------------------


def test_a_pending_entry_is_drawn_with_its_detail(capsys):
    """Whether or not verbose was asked for.

    Pending work is what somebody is about to approve. A line is enough to
    recognise a call and not enough to decide on one, so this is the one place a
    detail is not optional to show.
    """
    from aven.terminal.render import render_tray

    tray = Tray()
    tray.execute(buy, {"what": "a kettle"})

    render_tray(tray)

    shown = capsys.readouterr().out
    assert "buy a kettle" in shown, "the line"
    assert "a kettle" in shown and "¥38.00" in shown, "and the order under it"
    assert "Amazon" in shown


def test_work_that_already_happened_is_not_re_explained(capsys):
    """It is in the undoable list because it is done. A diff of it is noise."""
    from aven.harness.tools import Diff
    from aven.terminal.render import render_tray

    @tool(risk="reversible", preview="changed a file",
          detail=lambda **_: Diff(before="a", after="b"))
    def change() -> ToolResult:
        """Change."""
        return ToolResult(output="done", undo=lambda: None)

    tray = Tray()
    tray.execute(change, {})

    render_tray(tray)

    shown = capsys.readouterr().out
    assert "changed a file" in shown
    assert "+b" not in shown


def test_mail_is_shown_as_its_body_and_never_pre_approved():
    """A sent mail cannot be recalled, and "always allow mail to the accountant"
    is exactly how the wrong draft goes out."""
    import platform

    import pytest as _pytest

    if platform.system() != "Darwin":
        _pytest.skip("the mac tools are only built there")

    from aven.apps.cli_assistant.mac import mac_tools

    built = {tool.name: tool for tool in mac_tools()}

    assert built["send_mail"].pre_approvable is False
    assert built["draft_mail"].pre_approvable is True, "a draft can be deleted"

    body = built["send_mail"].detail_for(
        {"to": "a@b.c", "subject": "July", "body": "Attached."}
    )
    assert isinstance(body, Body)
    assert body.text == "Attached."
    assert "a@b.c" in body.title
