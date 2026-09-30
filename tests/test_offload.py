"""Big tool results, kept out of the conversation until they are wanted."""

import pytest

from aven.toolkit.offload import BIG, Keeper


@pytest.fixture
def keeper(tmp_path):
    return Keeper(tmp_path / "results")


def page(lines=900, mark="Livenerf: has it been nerfed yet?"):
    rows = [f"line {n}: a node with some text on it" for n in range(1, lines)]
    rows.append(f"line {lines}: {mark}")
    return "\n".join(rows)


def test_something_small_is_left_in_the_conversation(keeper):
    """Below the threshold, fetching it back costs a round trip and saves
    nothing. A directory listing asked for twice is slower and no cheaper."""
    said = "a short result"

    assert keeper.keep("c1", "list_dir", said) == said


def test_something_big_leaves_a_line_behind(keeper):
    """Measured: a ten-thousand-token result carried through ten turns costs
    about 21,500 token-equivalents. Offloaded it costs about 2,600."""
    big = page()
    stub = keeper.keep("c1", "browser_snapshot", big)

    assert len(stub) < 1_500, f"the stub is {len(stub)} characters"
    assert len(big) > BIG
    assert f"{len(big):,}" in stub, "it says how much was put aside"
    assert "recall(" in stub, "and how to get it"


def test_the_opening_is_in_the_stub(keeper):
    """Enough to tell whether the rest is worth fetching. A reference with no
    hint of what is behind it has to be opened to be judged, which is the
    round trip this avoids."""
    stub = keeper.keep("c1", "browser_snapshot", page())

    assert "line 1:" in stub


def test_the_whole_thing_is_still_on_disk(keeper):
    """The claim this project makes is that everything the agent did can be
    read afterwards. A result summarised and thrown away would break that."""
    big = page()
    keeper.keep("c1", "browser_snapshot", big)

    kept = keeper.held["browser_snapshot-c1"]
    assert kept.path.read_text() == big


def test_recall_finds_a_phrase_with_its_line_number(keeper):
    keeper.keep("c1", "browser_snapshot", page())
    recall = keeper.tools()[0]

    said = recall(id="browser_snapshot-c1", find="Livenerf").output

    assert "Livenerf" in said
    assert "900" in said, "the line number, so it can ask for a slice around it"
    assert len(said) < 600, "and not the whole file"


def test_recall_takes_a_slice(keeper):
    keeper.keep("c1", "browser_snapshot", page())
    recall = keeper.tools()[0]

    said = recall(id="browser_snapshot-c1", lines="10-12").output

    assert "line 10:" in said and "line 12:" in said
    assert "line 13:" not in said


def test_recall_says_when_a_phrase_is_not_there(keeper):
    """Rather than returning nothing, which reads as a broken tool."""
    keeper.keep("c1", "browser_snapshot", page())
    recall = keeper.tools()[0]

    assert "nothing matching" in recall(id="browser_snapshot-c1", find="zebra").output


def test_recall_names_what_it_does_have(keeper):
    """A model that asked for the wrong id can correct itself in one turn."""
    keeper.keep("c1", "browser_snapshot", page())
    recall = keeper.tools()[0]

    said = recall(id="wrong").output

    assert "browser_snapshot-c1" in said


def test_a_recall_that_would_undo_the_saving_is_cut(keeper):
    """A phrase matching every line would fetch the file back whole."""
    keeper.keep("c1", "browser_snapshot", page(lines=3_000))
    recall = keeper.tools()[0]

    said = recall(id="browser_snapshot-c1", find="a node").output

    assert len(said) < 14_000
    assert "cut" in said and "Ask for less" in said


def test_recall_reads_by_id_and_never_by_path(keeper):
    """It is not a second way into the filesystem. The file tools are the way,
    and they are scoped to the folders somebody named; a tool taking a path
    would be a hole with no sandbox on it, reachable by any model that read a
    page telling it to try."""
    recall = keeper.tools()[0]

    assert "path" not in recall.schema["properties"]
    assert set(recall.schema["properties"]) == {"id", "find", "lines"}
    assert "/etc/passwd" in recall(id="/etc/passwd").output  # treated as a name
    assert "no result called" in recall(id="/etc/passwd").output


def test_reading_it_back_needs_no_approval(keeper):
    """It is a result this conversation already produced."""
    assert keeper.tools()[0].risk == "read"


def test_nowhere_to_write_is_not_a_reason_to_lose_it(tmp_path):
    """A read-only home directory is a real thing."""
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    keeper = Keeper(blocked / "under-a-file")
    big = page()

    assert keeper.keep("c1", "browser_snapshot", big) == big, "kept, uncompressed"
