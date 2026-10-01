"""The sampling rule, tested against the thing that makes a sample worthless.

A sampled benchmark score means nothing if the sample could have been chosen
after seeing the results. These tests are what makes that checkable: the rule
is deterministic, it keeps the difficulty mix, and it does not take a prefix.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.terminal_bench.sample import (  # noqa: E402
    difficulty_of,
    groups,
    sample,
    spread,
)


def dataset(tmp_path, **counts):
    """A dataset directory with the given number of tasks per difficulty."""
    for difficulty, how_many in counts.items():
        for n in range(how_many):
            task = tmp_path / f"{difficulty}-{n:02d}"
            task.mkdir()
            (task / "task.yaml").write_text(
                f"instruction: do it\ndifficulty: {difficulty}\n", encoding="utf-8"
            )
    return tmp_path


def test_the_mix_matches_the_whole_set(tmp_path):
    """The failure this exists to prevent: thirty tasks, all of them easy."""
    where = dataset(tmp_path, easy=12, medium=44, hard=24)
    chosen = set(sample(30, where))
    by = groups(where)

    taken = {d: len(chosen & set(names)) for d, names in by.items()}
    assert sum(taken.values()) == 30
    # 12/80, 44/80, 24/80 of thirty, to the nearest whole task.
    assert taken == {"easy": 5, "medium": 16, "hard": 9}


def test_the_same_n_always_gives_the_same_tasks(tmp_path):
    """No seed, no shuffle: a seed is one more thing to try several of."""
    where = dataset(tmp_path, easy=12, medium=44, hard=24)
    assert sample(30, where) == sample(30, where)
    assert sample(30, where) != sample(29, where)


def test_the_parts_add_up_to_exactly_what_was_asked(tmp_path):
    """Three independent roundings sum to n-2 about a third of the time."""
    where = dataset(tmp_path, easy=12, medium=44, hard=24)
    for n in range(1, 81):
        assert len(sample(n, where)) == n, n


def test_asking_for_everything_gets_everything(tmp_path):
    where = dataset(tmp_path, easy=12, medium=44, hard=24)
    assert len(sample(80, where)) == 80
    assert len(sample(500, where)) == 80


def test_it_does_not_take_an_alphabetical_prefix(tmp_path):
    """The names are sorted, so the first n is a sample of one letter range."""
    names = [f"task-{n:02d}" for n in range(40)]
    taken = spread(names, 4)

    assert taken != names[:4]
    assert taken[0] == "task-00" and taken[-1] > "task-25", taken


def test_a_task_with_no_difficulty_counts_as_medium(tmp_path):
    """Dropping it would silently change the mix this file exists to keep."""
    bare = tmp_path / "unlabelled"
    bare.mkdir()
    (bare / "task.yaml").write_text("instruction: do it\n", encoding="utf-8")

    assert difficulty_of(bare) == "medium"
    assert groups(tmp_path) == {"medium": ["unlabelled"]}


def test_a_directory_that_is_not_a_task_is_ignored(tmp_path):
    dataset(tmp_path, easy=2)
    (tmp_path / "__pycache__").mkdir()

    assert groups(tmp_path) == {"easy": ["easy-00", "easy-01"]}


def test_no_tasks_at_all_is_an_empty_sample_not_a_crash(tmp_path):
    assert sample(30, tmp_path) == []
