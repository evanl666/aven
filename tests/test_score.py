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


def test_a_run_still_going_gets_a_sentence_not_a_traceback(tmp_path, capsys):
    """The commonest way to run this is too early, by accident."""
    from evals.terminal_bench.score import Unfinished, main

    going = tmp_path / "2026-01-01__00-00-00"
    going.mkdir()

    with pytest.raises(Unfinished):
        read_run(going)

    assert main([str(going)]) == 1
    out, err = capsys.readouterr()
    assert out == "", "a failure must not print a half table"
    assert "still going" in err


# --- prices ------------------------------------------------------------------
#
# The cost column is only as good as the table behind it, and a wrong table is
# invisible: every number still looks like money.


def test_the_table_comes_from_the_model_the_run_used():
    from evals.terminal_bench.score import prices_for

    haiku, said = prices_for("anthropic/claude-haiku-4-5-20251001")
    assert haiku["input"] == 1.00 and "haiku" in said

    sonnet, said = prices_for("anthropic/claude-sonnet-5")
    assert sonnet["input"] == 3.00 and "sonnet" in said

    assert haiku["input"] < sonnet["input"], "pricing a Haiku run as Sonnet triples the bill"


def test_an_unknown_model_overstates_rather_than_understates():
    """A bill that is too low is the one somebody acts on."""
    from evals.terminal_bench.score import BY_MODEL, prices_for

    unknown, said = prices_for("some-model-released-after-this-was-written")
    assert "NO PRICES" in said
    assert unknown["input"] == max(p["input"] for p in BY_MODEL.values())


def test_cache_read_is_a_tenth_and_cache_write_is_a_quarter_more():
    """The whole reason the hit rate is worth printing."""
    from evals.terminal_bench.score import BY_MODEL

    for name, price in BY_MODEL.items():
        assert price["cache_read"] == pytest.approx(price["input"] * 0.1), name
        assert price["cache_write"] == pytest.approx(price["input"] * 1.25), name


def test_scoring_a_run_prices_it_with_that_runs_model(tmp_path):
    from evals.terminal_bench.score import report

    run = tmp_path / "2026-01-01__00-00-00"
    (run / "alpha" / "t" / "sessions").mkdir(parents=True)
    (run / "alpha" / "t" / "sessions" / "agent.log").write_text(
        "  3 requests · in 1000000 (cache read 0 · wrote 0) · out 0\n", encoding="utf-8"
    )
    (run / "results.json").write_text(
        '{"results": [{"task_id": "alpha", "is_resolved": true}]}', encoding="utf-8"
    )
    (run / "run_metadata.json").write_text(
        '{"model_name": "anthropic/claude-haiku-4-5-20251001"}', encoding="utf-8"
    )

    (alpha,) = read_run(run)
    assert alpha.cost == pytest.approx(1.00), "a million fresh input tokens of Haiku"
    assert "haiku" in report([alpha])


def test_a_more_specific_model_name_wins_over_one_it_contains(monkeypatch):
    """Why the fragments are matched longest-first.

    Nothing in today's table overlaps, so this ordering is insurance against
    the next name rather than something the current names need. Tested anyway:
    insurance nobody checks is the kind that is quietly removed.
    """
    import evals.terminal_bench.score as score

    monkeypatch.setattr(score, "BY_MODEL", {
        "sonnet-5": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3},
        "sonnet-5-mini": {"input": 0.5, "output": 2.5, "cache_write": 0.625, "cache_read": 0.05},
    })

    mini, said = score.prices_for("anthropic/claude-sonnet-5-mini")
    assert mini["input"] == 0.5, f"matched the shorter fragment: {said}"


# --- the table ---------------------------------------------------------------


def test_a_row_is_never_wider_than_the_table(tmp_path):
    """The decisive check, and the one a parser cannot do.

    704 requests beside 13,952,143 tokens printed as "70413,952,143": a field
    too narrow for its number does not truncate, it grows, and the number
    beside it gets pushed into contact. Whether two numbers are touching is
    impossible to tell by reading the characters - "70413" is a perfectly good
    number - but an overflowing field always makes the line longer than the
    rule above it. So the width is the test.

    A first version of this test looked at the characters and skipped every
    digit that followed a digit, which is exactly the case it was meant to
    catch. It passed against the bug.
    """
    from evals.terminal_bench.score import Task, report

    # Big enough that the totals reach the magnitude that broke it: nine-digit
    # token counts, summed over thirty tasks.
    big = [
        Task(
            name=f"task-{n:02d}", resolved=n % 3 == 0, requests=99,
            total_input=999_999_999, cache_read=999_999_998,
            cache_write=1, output=9_999_999,
        )
        for n in range(30)
    ]

    printed = report(big).splitlines()
    rule = len(printed[1])
    for line in printed:
        if line.startswith(("task-", "total")):
            assert len(line) == rule, f"{len(line)} wide, rule is {rule}: {line!r}"


def test_the_header_lines_up_with_the_rows(tmp_path):
    """Header, rows and total are one set of widths or they drift apart."""
    from evals.terminal_bench.score import Task, report

    lines = report([
        Task(name="a", resolved=True, requests=5, total_input=100,
             cache_read=50, cache_write=10, output=7),
    ]).splitlines()

    header, rule, row, rule2, total = lines[0], lines[1], lines[2], lines[3], lines[4]
    assert len(rule) == len(rule2)
    assert len(header) == len(rule) == len(row) == len(total), (
        f"header {len(header)}, rule {len(rule)}, row {len(row)}, total {len(total)}"
    )


def test_every_cell_stays_a_separate_word(tmp_path):
    """Equal line widths are not enough - "70413,952,143" is 13 wide either way.

    Dropping the gap between columns keeps every line exactly as wide as the
    rule and still runs two numbers together, so the width test above passes
    against it. This one splits the row back into words and insists they are
    the cells that went in.
    """
    from evals.terminal_bench.score import NAME, _cells, report

    big = [
        Task(
            name=f"task-{n:02d}", resolved=True, requests=999,
            total_input=123_456_789, cache_read=123_456_788,
            cache_write=1, output=1_234_567,
        )
        for n in range(30)
    ]

    printed = report(big).splitlines()
    for line, task in zip(printed[2:], sorted(big, key=lambda x: (-x.cost, x.name))):
        if not line.startswith("task-"):
            continue
        assert line[NAME:].split() == ["ok", *_cells(task)], line

    total = next(x for x in printed if x.startswith("total"))
    # Seven words: nothing merged, and the percentage did not swallow a digit.
    assert len(total.split()) == 7, total
