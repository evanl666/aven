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


# --- more than one root ------------------------------------------------------
#
# A desktop assistant is asked to file the invoices from Downloads into
# Documents, which is two folders in one sentence. Doing that with a single root
# meant making the root the home directory, at which point the sandbox stopped
# meaning anything.


@pytest.fixture
def desk(tmp_path):
    """Two roots, the way a desktop run has them."""
    for name in ("Downloads", "Documents"):
        (tmp_path / name).mkdir()
    (tmp_path / "Downloads" / "invoice.pdf").write_text("38.00")
    (tmp_path / "Documents" / "notes.md").write_text("mine")
    (tmp_path / "Private").mkdir()
    (tmp_path / "Private" / "secret.txt").write_text("not yours")
    return tmp_path


@pytest.fixture
def spanning(desk):
    return {t.name: t for t in file_tools(desk / "Downloads", desk / "Documents")}


def test_a_bare_path_still_means_the_first_root(spanning):
    """The working folder, unchanged - that is what a single root always was."""
    assert spanning["read_file"](path="invoice.pdf").output == "38.00"


def test_a_second_root_is_reached_by_its_name(spanning):
    assert spanning["read_file"](path="Documents/notes.md").output == "mine"


def test_a_folder_that_is_not_a_root_is_still_refused(spanning):
    """Adding roots widens the boundary to exactly the roots, and no further."""
    with pytest.raises(Outside):
        spanning["read_file"](path="../Private/secret.txt")


def test_an_absolute_path_into_a_root_is_accepted(desk, spanning):
    """How a model refers back to a path it saw in a listing."""
    full = str(desk / "Documents" / "notes.md")

    assert spanning["read_file"](path=full).output == "mine"


def test_an_absolute_path_outside_every_root_is_refused(desk, spanning):
    with pytest.raises(Outside):
        spanning["read_file"](path=str(desk / "Private" / "secret.txt"))


def test_moving_between_roots_is_the_whole_point(desk, spanning):
    result = spanning["move_file"](src="invoice.pdf", dst="Documents/invoices/38.pdf")

    assert (desk / "Documents" / "invoices" / "38.pdf").read_text() == "38.00"
    assert not (desk / "Downloads" / "invoice.pdf").exists()
    assert "Documents/invoices/38.pdf" in result.output


def test_undoing_a_move_between_roots_puts_it_back(desk, spanning):
    """Including the folder invented in the other root on the way in."""
    result = spanning["move_file"](src="invoice.pdf", dst="Documents/invoices/38.pdf")

    result.undo()

    assert (desk / "Downloads" / "invoice.pdf").read_text() == "38.00"
    assert not (desk / "Documents" / "invoices").exists(), "the folder went too"


def test_a_preview_says_which_root_when_there_is_more_than_one(desk, spanning):
    """Otherwise "notes.md → notes.md" is all the person gets to read."""
    moved = spanning["move_file"](src="invoice.pdf", dst="Documents/paid.pdf").output

    assert "Downloads/invoice.pdf" in moved
    assert "Documents/paid.pdf" in moved


def test_a_single_root_preview_stays_bare(box, tools):
    """One root reads exactly as it always did - no name in front of anything."""
    (box / "Downloads" / "b.pdf").write_text("x")

    moved = tools["move_file"](src="Downloads/b.pdf", dst="Downloads/c.pdf").output

    assert "Downloads/b.pdf → Downloads/c.pdf" in moved


def test_a_root_name_is_not_read_as_one_when_there_is_only_one(box, tools):
    """The first segment has never named a root, and must not start to.

    With Downloads as the only root, "Downloads/a.pdf" has always meant
    Downloads/Downloads/a.pdf and code written against that must not change
    meaning underneath it.
    """
    with pytest.raises(FileNotFoundError):
        tools["read_file"](path="Downloads/Downloads/a.pdf")

    assert tools["read_file"](path="Downloads/a.pdf").output == "hello"


def test_two_roots_with_the_same_name_are_told_apart(tmp_path):
    for parent in ("work", "home"):
        (tmp_path / parent / "notes").mkdir(parents=True)
        (tmp_path / parent / "notes" / "a.md").write_text(parent)

    built = {
        t.name: t
        for t in file_tools(tmp_path / "work" / "notes", tmp_path / "home" / "notes")
    }

    assert built["read_file"](path="a.md").output == "work"
    assert built["read_file"](path="home-notes/a.md").output == "home"


def test_no_roots_at_all_is_a_mistake_worth_refusing():
    with pytest.raises(ValueError):
        file_tools()
