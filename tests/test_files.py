"""Tests for the file toolset, especially its boundary."""

import pytest

from aven.tools import file_tools
from aven.tools.files import Outside


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
