"""Tests for the file toolset, especially its boundary."""

import pytest

from aven.toolkit import file_tools
from aven.toolkit.files import Outside


@pytest.fixture
def box(tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.pdf").write_text("hello")
    return tmp_path


@pytest.fixture
def tools(box):
    return {t.name: t for t in file_tools(box)}


@pytest.mark.parametrize(
    "escape",
    ["../outside.txt", "/etc/passwd", "Downloads/../../secret", "~/.ssh/id_rsa"],
)
def test_paths_outside_the_root_are_refused(tools, escape):
    """A model that read a hostile page will eventually ask for this."""
    with pytest.raises(Outside):
        tools["read_file"](path=escape)


def test_a_symlink_pointing_out_is_refused(box, tools):
    """resolve() is what makes this work - the string alone looks harmless."""
    (box / "link").symlink_to("/etc")
    with pytest.raises(Outside):
        tools["read_file"](path="link/hosts")


def test_read_and_list(box, tools):
    assert tools["read_file"](path="Downloads/a.pdf").output == "hello"
    assert "a.pdf" in tools["list_dir"](path="Downloads").output


def test_move_and_undo(box, tools):
    result = tools["move_file"](src="Downloads/a.pdf", dst="archive/a.pdf")

    assert (box / "archive" / "a.pdf").exists()
    assert not (box / "Downloads" / "a.pdf").exists()

    result.undo()
    assert (box / "Downloads" / "a.pdf").exists()


def test_move_refuses_to_clobber(box, tools):
    (box / "b.pdf").write_text("other")
    with pytest.raises(FileExistsError):
        tools["move_file"](src="Downloads/a.pdf", dst="b.pdf")


def test_write_undo_restores_the_previous_content(box, tools):
    result = tools["write_file"](path="Downloads/a.pdf", content="new")

    assert (box / "Downloads" / "a.pdf").read_text() == "new"
    result.undo()
    assert (box / "Downloads" / "a.pdf").read_text() == "hello"


def test_write_undo_removes_a_file_it_created(box, tools):
    result = tools["write_file"](path="notes.md", content="x")

    assert (box / "notes.md").exists()
    result.undo()
    assert not (box / "notes.md").exists()


def test_delete_goes_to_the_trash_and_comes_back(box, tools):
    result = tools["delete_file"](path="Downloads/a.pdf")

    assert not (box / "Downloads" / "a.pdf").exists()
    result.undo()
    assert (box / "Downloads" / "a.pdf").read_text() == "hello"


def test_delete_is_reversible_so_it_needs_no_approval(tools):
    assert tools["delete_file"].risk == "reversible"
    assert tools["list_dir"].risk == "read"


def test_undo_removes_a_folder_it_invented(box, tools):
    result = tools["move_file"](src="Downloads/a.pdf", dst="报销/2026/a.pdf")
    assert (box / "报销" / "2026").is_dir()

    result.undo()

    assert not (box / "报销").exists(), "the whole invented branch goes"
    assert (box / "Downloads" / "a.pdf").exists()


def test_undo_keeps_a_folder_that_already_existed(box, tools):
    (box / "archive").mkdir()
    result = tools["move_file"](src="Downloads/a.pdf", dst="archive/a.pdf")

    result.undo()

    assert (box / "archive").is_dir(), "aven did not create it, so it stays"


def test_undo_keeps_a_folder_someone_else_put_a_file_in(box, tools):
    result = tools["write_file"](path="notes/today.md", content="x")
    (box / "notes" / "other.md").write_text("not ours")

    result.undo()

    assert (box / "notes").is_dir(), "not empty, not ours to delete"
    assert (box / "notes" / "other.md").exists()


# --- editing -----------------------------------------------------------------


@pytest.fixture
def notes(box):
    (box / "notes.md").write_text("# 报销\n\n- 滴滴 38.50\n- 美团 126.00\n\n合计 164.50\n")
    return box / "notes.md"


def test_edit_replaces_only_the_passage_given(notes, tools):
    tools["edit_file"](path="notes.md", old="合计 164.50", new="合计 164.50 元")

    text = notes.read_text()
    assert "合计 164.50 元" in text
    assert "- 滴滴 38.50" in text, "nothing else moved"


def test_edit_undo_restores_the_whole_file(notes, tools):
    result = tools["edit_file"](path="notes.md", old="- 美团 126.00", new="- 美团 0")
    before = notes.read_text()

    result.undo()

    assert notes.read_text() != before
    assert "- 美团 126.00" in notes.read_text()


def test_a_passage_that_is_not_there_says_why_it_might_not_be(notes, tools):
    """The model is working from read_file's output, so a miss is usually
    whitespace. The message has to point at that."""
    from aven.toolkit.files import NotFound

    with pytest.raises(NotFound, match="indentation"):
        tools["edit_file"](path="notes.md", old="-  美团 126.00", new="x")


def test_a_passage_that_appears_twice_is_refused(notes, tools):
    """Guessing which one was meant is worse than asking for a longer passage."""
    from aven.toolkit.files import Ambiguous

    with pytest.raises(Ambiguous, match="appears 2 times"):
        tools["edit_file"](path="notes.md", old="- ", new="* ")


def test_a_failed_edit_leaves_the_file_alone(notes, tools):
    from aven.toolkit.files import NotFound

    before = notes.read_text()
    with pytest.raises(NotFound):
        tools["edit_file"](path="notes.md", old="不存在的文字", new="x")

    assert notes.read_text() == before


def test_edit_is_scoped_to_the_root_like_everything_else(tools):
    with pytest.raises(Outside):
        tools["edit_file"](path="../outside.md", old="a", new="b")


def test_the_preview_shows_what_is_being_replaced_without_touching_the_file(notes, tools):
    shown = tools["edit_file"].preview(
        {"path": "notes.md", "old": "合计 164.50", "new": "合计 164.50 元"}
    )

    assert "notes.md" in shown and "合计 164.50" in shown
    assert notes.read_text().endswith("合计 164.50\n"), "preview read nothing and wrote nothing"
