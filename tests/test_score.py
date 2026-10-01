"""The cost report reads a number aven printed. This is that coupling, tested.

`evals/terminal_bench/score.py` recovers token counts by matching the usage
line out of a task log. Nothing enforces that the line still looks the way the
pattern expects - change the wording in the text catalogue and every task
silently reports zero tokens and $0.00, which is a believable number and the
worst kind of wrong.

So the test does not hardcode the line. It asks aven to produce one and checks
the pattern against that.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aven.model.claude import Usage  # noqa: E402
from evals.terminal_bench.score import PRICES, Task, read_run, usage_in  # noqa: E402


class Counted:
    """What the provider hands back on one request."""

    def __init__(self, fresh=0, read=0, written=0, out=0):
        self.input_tokens = fresh
        self.cache_read_input_tokens = read
        self.cache_creation_input_tokens = written
        self.output_tokens = out


def test_the_pattern_matches_a_line_aven_actually_produces(tmp_path):
    usage = Usage()
    usage.add(Counted(fresh=12, read=9_176, written=2_814, out=759))
    usage.add(Counted(fresh=0, read=4_000, written=100, out=200))

    log = tmp_path / "agent.log"
    log.write_text(f"some earlier output\n  {usage}\nroot@host:/app# \n", encoding="utf-8")

    found = usage_in(log)
    assert found is not None, f"the pattern no longer matches: {usage}"

    requests, total_input, read, written, out = found
    assert requests == usage.requests
    assert total_input == usage.total_input
    assert read == usage.cached_tokens
    assert written == usage.written_tokens
    assert out == usage.output_tokens


def test_escape_sequences_and_carriage_returns_do_not_hide_it(tmp_path):
    """asciinema records a terminal, not a text file."""
    usage = Usage()
    usage.add(Counted(fresh=0, read=100, written=50, out=10))

    log = tmp_path / "agent.log"
    log.write_text(f"\x1b[2K\x1b[32mworking\x1b[0m\r\x1b[2m  {usage}\x1b[0m\r\n", encoding="utf-8")

    assert usage_in(log) is not None


def test_the_last_line_wins(tmp_path):
    """A retried task printed one per attempt; the result came from the last."""
    log = tmp_path / "agent.log"
    log.write_text(
        "  1 requests · in 100 (cache read 0 · wrote 100) · out 1\n"
        "  7 requests · in 900 (cache read 800 · wrote 100) · out 70\n",
        encoding="utf-8",
    )
    assert usage_in(log)[0] == 7


def test_no_usage_line_is_not_a_crash(tmp_path):
    """A container where the install failed never printed one."""
    log = tmp_path / "agent.log"
    log.write_text("bash: aven-code: command not found\nINSTALL_FAIL_STATUS\n", encoding="utf-8")
    assert usage_in(log) is None


def test_fresh_input_is_what_was_not_cached():
    task = Task(name="x", resolved=True, total_input=1_000, cache_read=700, cache_write=200)
    assert task.fresh == 100


def test_fresh_input_never_goes_negative():
    """The three numbers come from the provider and are not ours to trust."""
    task = Task(name="x", resolved=True, total_input=100, cache_read=700, cache_write=200)
    assert task.fresh == 0


def test_cost_prices_each_kind_of_token_differently():
    task = Task(
        name="x", resolved=True,
        total_input=1_000_000, cache_read=0, cache_write=0, output=0,
    )
    assert task.cost == pytest.approx(PRICES["input"])

    cached = Task(
        name="x", resolved=True,
        total_input=1_000_000, cache_read=1_000_000, cache_write=0, output=0,
    )
    assert cached.cost == pytest.approx(PRICES["cache_read"])
    assert cached.cost < task.cost / 5, "a cache read has to be much cheaper"


def test_a_task_that_never_ran_is_not_averaged_in():
    """Otherwise a broken container improves the cost per task."""
    assert Task(name="x", resolved=False).measured is False
    assert Task(name="x", resolved=False, requests=1).measured is True


def test_read_run_pairs_results_with_the_logs(tmp_path):
    run = tmp_path / "2026-01-01__00-00-00"
    (run / "alpha" / "trial" / "sessions").mkdir(parents=True)
    (run / "alpha" / "trial" / "sessions" / "agent.log").write_text(
        "  3 requests · in 500 (cache read 400 · wrote 50) · out 20\n", encoding="utf-8"
    )
    (run / "results.json").write_text(
        '{"results": [{"task_id": "alpha", "is_resolved": true},'
        ' {"task_id": "beta", "is_resolved": false}]}',
        encoding="utf-8",
    )

    alpha, beta = read_run(run)
    assert alpha.resolved and alpha.requests == 3 and alpha.total_input == 500
    assert not beta.resolved and not beta.measured
