"""Tests for turns nobody typed.

`due()` takes the time as an argument, which is the whole reason this file can
exist: a test says "it is 08:31 on Tuesday" instead of waiting until it is.
Anything that reads the clock internally can only be tested by sleeping, and a
test that sleeps is a test nobody runs.
"""

import json
import time
from pathlib import Path

import pytest

from aven.harness.triggers import (
    GRACE,
    Memory,
    Trigger,
    due,
    keep,
    load,
    remember,
)


def at(day: int, hour: int, minute: int) -> float:
    """A local timestamp, so the tests read as times rather than epochs."""
    return time.mktime((2026, 9, day, hour, minute, 0, 0, 0, -1))


def written(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "triggers.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- reading the file --------------------------------------------------------


def test_a_daily_trigger_is_read(tmp_path):
    path = written(tmp_path, '''
[[trigger]]
name = "morning"
at = "08:30"
prompt = "summarise what is coming today"
''')

    loaded = load(path)

    assert len(loaded) == 1
    assert loaded[0].name == "morning"
    assert loaded[0].minutes() == 8 * 60 + 30
    assert loaded[0].source == "trigger:cron"


def test_a_folder_trigger_is_read(tmp_path):
    path = written(tmp_path, '''
[[trigger]]
watch = "~/Downloads"
prompt = "something landed; file it if it is an invoice"
''')

    loaded = load(path)

    assert loaded[0].watch == Path("~/Downloads").expanduser()
    assert loaded[0].source == "trigger:fs"
    assert loaded[0].name == "trigger-1", "named by position when unnamed"


def test_no_file_means_no_triggers(tmp_path):
    assert load(tmp_path / "nothing.toml") == []


def test_a_broken_file_fires_nothing_rather_than_refusing_to_start(tmp_path):
    assert load(written(tmp_path, "[[trigger]\nat = oops")) == []


@pytest.mark.parametrize(
    "why,text",
    [
        ("nothing to ask", '[[trigger]]\nat = "08:30"\n'),
        ("no moment to ask it", '[[trigger]]\nprompt = "x"\n'),
        ("both a time and a folder", '[[trigger]]\nat = "08:30"\nwatch = "~"\nprompt = "x"\n'),
        ("not a time of day", '[[trigger]]\nat = "8.30"\nprompt = "x"\n'),
        ("not a time of day either", '[[trigger]]\nat = "25:00"\nprompt = "x"\n'),
    ],
)
def test_a_trigger_that_could_never_fire_is_dropped(tmp_path, why, text):
    assert load(written(tmp_path, text)) == [], why


# --- the clock ---------------------------------------------------------------


def morning() -> Trigger:
    return Trigger(name="morning", prompt="summarise", at="08:30")


def test_nothing_fires_before_its_time():
    memory = Memory()

    assert due([morning()], memory, at(26, 8, 29)) == []
    assert memory.fired == {}, "and nothing is recorded"


def test_it_fires_at_its_time():
    memory = Memory()

    assert [t.name for t in due([morning(), ], memory, at(26, 8, 30))] == ["morning"]
    assert "morning" in memory.fired


def test_it_does_not_fire_twice_in_one_day():
    memory = Memory()
    due([morning()], memory, at(26, 8, 30))

    assert due([morning()], memory, at(26, 8, 31)) == []
    assert due([morning()], memory, at(26, 17, 0)) == []


def test_it_fires_again_the_next_day():
    memory = Memory()
    due([morning()], memory, at(26, 8, 30))

    assert len(due([morning()], memory, at(27, 8, 30))) == 1


def test_the_next_day_is_a_calendar_day_not_a_stretch_of_hours():
    """Otherwise 08:30 drifts later every morning until it is the afternoon."""
    memory = Memory()
    due([morning()], memory, at(26, 8, 35))  # a little late today

    assert len(due([morning()], memory, at(27, 8, 30))) == 1, "on time tomorrow"


def test_a_machine_that_was_asleep_still_catches_up():
    """Nobody wants the morning summary skipped because the lid was shut."""
    memory = Memory()

    assert len(due([morning()], memory, at(26, 9, 45))) == 1


def test_a_machine_that_was_off_all_day_does_not_do_it_at_midnight():
    memory = Memory()
    late = at(26, 8, 30) + GRACE + 600

    assert due([morning()], memory, late) == []


# --- the folder --------------------------------------------------------------


def watching(folder: Path) -> Trigger:
    return Trigger(name="downloads", prompt="file it", watch=folder)


def test_the_first_look_at_a_folder_fires_nothing(tmp_path):
    """A trigger added today should not react to files from last year."""
    (tmp_path / "old.pdf").write_text("x")
    memory = Memory()

    assert due([watching(tmp_path)], memory, at(26, 12, 0)) == []
    assert memory.seen["downloads"] == ["old.pdf"], "but it is noted"


def test_a_new_file_fires_it(tmp_path):
    memory = Memory()
    due([watching(tmp_path)], memory, at(26, 12, 0))

    (tmp_path / "new.pdf").write_text("x")

    assert len(due([watching(tmp_path)], memory, at(26, 12, 1))) == 1


def test_the_same_files_do_not_fire_it_again(tmp_path):
    memory = Memory()
    due([watching(tmp_path)], memory, at(26, 12, 0))
    (tmp_path / "new.pdf").write_text("x")
    due([watching(tmp_path)], memory, at(26, 12, 1))

    assert due([watching(tmp_path)], memory, at(26, 12, 2)) == []


def test_a_file_rewritten_in_place_is_not_something_landing(tmp_path):
    """Names, not modification times: saving a file again is not an arrival."""
    (tmp_path / "notes.md").write_text("one")
    memory = Memory()
    due([watching(tmp_path)], memory, at(26, 12, 0))

    (tmp_path / "notes.md").write_text("two")

    assert due([watching(tmp_path)], memory, at(26, 12, 1)) == []


def test_a_hidden_file_is_not_an_arrival(tmp_path):
    memory = Memory()
    due([watching(tmp_path)], memory, at(26, 12, 0))

    (tmp_path / ".DS_Store").write_text("x")

    assert due([watching(tmp_path)], memory, at(26, 12, 1)) == []


def test_a_folder_that_is_not_there_fires_nothing(tmp_path):
    memory = Memory()

    assert due([watching(tmp_path / "gone")], memory, at(26, 12, 0)) == []


def test_a_removed_file_is_not_an_arrival(tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    memory = Memory()
    due([watching(tmp_path)], memory, at(26, 12, 0))

    (tmp_path / "a.pdf").unlink()

    assert due([watching(tmp_path)], memory, at(26, 12, 1)) == []


# --- what is remembered between runs -----------------------------------------


def test_the_state_survives_a_round_trip(tmp_path):
    path = tmp_path / "state.json"
    memory = Memory(fired={"morning": 1.0}, seen={"downloads": ["a.pdf"]})

    keep(memory, path)

    assert remember(path) == memory


def test_no_state_yet_is_not_an_error(tmp_path):
    assert remember(tmp_path / "nothing.json") == Memory()


def test_a_corrupt_state_starts_over_rather_than_raising(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")

    assert remember(path) == Memory()


def test_a_state_that_cannot_be_written_does_not_stop_anything(tmp_path):
    """A lost write costs one trigger firing twice. Stopping would turn a full
    disk into a broken assistant."""
    blocked = tmp_path / "file" / "state.json"
    (tmp_path / "file").write_text("I am not a directory", encoding="utf-8")

    keep(Memory(fired={"a": 1.0}), blocked)  # must not raise


def test_deciding_and_recording_are_one_step():
    """A decision recorded a moment later is one that can fire twice if anything
    goes wrong in between."""
    memory = Memory()

    due([morning()], memory, at(26, 8, 30))

    assert memory.fired["morning"] == at(26, 8, 30)


def test_several_triggers_are_judged_independently(tmp_path):
    memory = Memory()
    both = [morning(), watching(tmp_path)]
    due(both, memory, at(26, 8, 0))  # too early for one, first look for the other

    (tmp_path / "new.pdf").write_text("x")
    ready = due(both, memory, at(26, 8, 30))

    assert sorted(t.name for t in ready) == ["downloads", "morning"]
