"""What a Terminal-Bench run cost, task by task.

Terminal-Bench reports accuracy and nothing else. Accuracy is mostly a
statement about the model: given a working shell, Sonnet either knows how to
fix the pipeline or it does not, and no amount of harness care changes that.

Cost is the number that is mostly about the harness. The harness decides how
many tokens it takes to get there - how long the tool descriptions are, whether
the prefix stays byte-identical so it can be read from cache at a tenth of the
price, whether a 40,000-character page sits in the conversation for ten turns
after anybody needed it. Every economy aven claims shows up here and nowhere
else.

So this reads the usage line aven prints at the end of every task and puts it
beside whether the task passed.

The numbers come from the provider, through `Usage`, by way of the line printed
into the task's own agent.log:

    11 requests · in 65127 (cache read 53784 · wrote 11322) · out 2059

Reading them back out of a log is not elegant. It is, however, the same number
the provider billed, and the alternative - having the adapter write a sidecar
file - means trusting the adapter about its own cost.

## The three numbers worth arguing about

**Cost per solved task**, not cost per task. An agent that gives up cheaply on
everything has an excellent cost per task.

**Cache hit rate.** On a multi-turn task nearly all input should be a cache
read. A low rate means something upstream is changing bytes between turns -
usually a timestamp or a reordered tool list - and it is a 10x price difference
on the largest number in the run.

**Output tokens.** The one aven cannot cache or offload. A harness that makes
the model narrate its way to an answer pays full price for every word.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Per million tokens, in dollars, by model. An assumption, not a measurement -
# the run records how many tokens it used and never what it was charged, so
# this table is the one thing here that can be out of date without anything
# failing. Printed with the results for that reason, so a number from this
# script can always be checked against what the invoice actually said.
#
# Keyed by a fragment of the model name, matched longest-first, because the
# name in run_metadata.json carries a provider prefix and sometimes a date
# suffix: "anthropic/claude-haiku-4-5-20251001".
#
# cache_write is 1.25x input and cache_read is 0.1x, which is why a run with a
# low hit rate costs so much more than the same run with a high one.
BY_MODEL = {
    "haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
    "sonnet-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "opus-5": {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
}

# What a Task costs with. Module-level because Task.cost is a property and the
# alternative is threading a price table through every row; set once, from the
# run's own metadata, before anything is priced.
#
# Defaults to the most expensive table in the book. A missing price has to
# overstate the bill, never understate it: a number that is too low is one
# somebody acts on.
PRICES = dict(BY_MODEL["opus-5"])


def prices_for(model: str) -> tuple[dict[str, float], str]:
    """The price table for a model name, and what to say about the choice."""
    for fragment in sorted(BY_MODEL, key=len, reverse=True):
        if fragment in model:
            return dict(BY_MODEL[fragment]), f"prices for {fragment}"
    return (
        dict(BY_MODEL["opus-5"]),
        f"NO PRICES FOR {model!r} - billed here at the most expensive table in "
        "the book, so every cost below is an upper bound, not the bill",
    )

# The line Usage.__str__ produces, as it reaches the log. Written against the
# English catalogue: an eval run is machine-to-machine and sets no --lang.
USAGE = re.compile(
    r"(\d+)\s+requests\s+·\s+in\s+(\d+)\s+"
    r"\(cache read\s+(\d+)\s+·\s+wrote\s+(\d+)\)\s+·\s+out\s+(\d+)"
)

# asciinema writes the log with escape sequences and carriage returns in it.
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class Task:
    name: str
    resolved: bool
    requests: int = 0
    total_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0

    @property
    def fresh(self) -> int:
        """Input billed at full price: neither read from nor written to cache."""
        return max(0, self.total_input - self.cache_read - self.cache_write)

    @property
    def cost(self) -> float:
        return (
            self.fresh * PRICES["input"]
            + self.cache_read * PRICES["cache_read"]
            + self.cache_write * PRICES["cache_write"]
            + self.output * PRICES["output"]
        ) / 1_000_000

    @property
    def hit_rate(self) -> float:
        return self.cache_read / self.total_input if self.total_input else 0.0

    @property
    def measured(self) -> bool:
        """Whether aven ever got far enough to report anything.

        A task whose container failed to install aven has no usage line, and
        averaging it in as zero would quietly improve the cost per task every
        time the harness broke.
        """
        return self.requests > 0


def usage_in(log: Path) -> tuple[int, int, int, int, int] | None:
    """The last usage line in a task log, or None if aven never printed one."""
    try:
        text = ANSI.sub("", log.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    found = USAGE.findall(text.replace("\r", "\n"))
    if not found:
        return None
    # The last one: a task that was retried printed one line per attempt, and
    # the attempt that counts is the one the result was read from.
    return tuple(int(n) for n in found[-1])  # type: ignore[return-value]


class Unfinished(Exception):
    """There is no results.json, so there is nothing to score yet."""


# What the chosen price table is, for printing. Set by read_run.
PRICED = "prices not chosen yet"


def model_of(where: Path) -> str:
    """Which model the run used, as the run itself recorded it."""
    try:
        meta = json.loads((where / "run_metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(meta.get("model_name") or "")


def read_run(where: Path) -> list[Task]:
    summary = where / "results.json"
    if not summary.is_file():
        # The ordinary case while a run is still going, so it gets a sentence
        # rather than a traceback. Terminal-Bench writes this file once, at the
        # end; a run in progress has the directory and not the file.
        raise Unfinished(
            f"{where} has no results.json - the run is still going, or it died "
            "before writing one"
        )

    results = json.loads(summary.read_text(encoding="utf-8"))

    # The prices come from the run, not from whoever is reading it. A Haiku run
    # scored with Sonnet's table reports a bill three times the real one, and
    # nothing about the output would look wrong.
    global PRICES, PRICED
    PRICES, PRICED = prices_for(model_of(where))

    tasks: list[Task] = []
    for row in results.get("results", []):
        name = row.get("task_id", "?")
        task = Task(name=name, resolved=bool(row.get("is_resolved")))

        logs = sorted((where / name).rglob("agent.log")) if (where / name).is_dir() else []
        counted = usage_in(logs[-1]) if logs else None
        if counted is not None:
            (
                task.requests,
                task.total_input,
                task.cache_read,
                task.cache_write,
                task.output,
            ) = counted

        tasks.append(task)

    return tasks


# The leftmost column, which is a name rather than a number.
NAME, MARK = 34, 4

# The numeric columns are NOT fixed. They were, in three separate format
# strings - which is three places that have to agree about six numbers, and
# they stopped agreeing the first time a total went past eight digits: 704
# requests beside 13,952,143 tokens printed as "70413,952,143", because a
# field too narrow for its number does not truncate, it grows, and pushes the
# number next to it into contact.
#
# Widening the fields only moves the magnitude at which that happens. So the
# table measures its own contents instead: every column is as wide as the
# widest thing in it, which makes overflow unrepresentable rather than
# unlikely. A thirty-task run and a three-hundred-task run both line up.
COLUMNS = ("req", "input", "cached", "out", "hit", "$")
GAP = 2


def _cells(task: Task) -> list[str]:
    return [
        f"{task.requests:,}",
        f"{task.total_input:,}",
        f"{task.cache_read:,}",
        f"{task.output:,}",
        f"{task.hit_rate:.0%}",
        f"{task.cost:.3f}",
    ]


def report(tasks: list[Task]) -> str:
    rows = sorted(tasks, key=lambda x: (-x.cost, x.name))
    measured = [x for x in tasks if x.measured]
    solved = [x for x in tasks if x.resolved]

    spent = sum(x.cost for x in tasks)
    read = sum(x.cache_read for x in tasks)
    went_in = sum(x.total_input for x in tasks)
    summed = [
        f"{sum(x.requests for x in tasks):,}",
        f"{went_in:,}",
        f"{read:,}",
        f"{sum(x.output for x in tasks):,}",
        f"{(read / went_in if went_in else 0):.0%}",
        f"{spent:.2f}",
    ]

    # Measured over the header, every measured row, and the total - the total
    # is wider than any row, which is what the fixed widths kept getting wrong.
    body = [_cells(task) for task in rows if task.measured] + [summed]
    widths = [
        max(len(COLUMNS[n]), *(len(cells[n]) for cells in body)) + GAP
        for n in range(len(COLUMNS))
    ]
    rule = NAME + MARK + sum(widths)

    def line(start: str, cells: list[str]) -> str:
        return start + "".join(
            f"{cell:>{width}}" for cell, width in zip(cells, widths)
        )

    out = [line(f"{'task':<{NAME}}{'':<{MARK}}", list(COLUMNS)), "-" * rule]

    for task in rows:
        start = f"{task.name:<{NAME}}{'ok ' if task.resolved else '   ':<{MARK}}"
        if not task.measured:
            out.append(start + f"{'- never ran -':>{sum(widths)}}")
            continue
        out.append(line(start, _cells(task)))

    out += [
        "-" * rule,
        line(f"{'total':<{NAME}}{'':<{MARK}}", summed),
        "",
        f"  accuracy            {len(solved)}/{len(tasks)}"
        f"  ({len(solved) / len(tasks):.1%})" if tasks else "  no tasks",
        f"  total cost          ${spent:.2f}",
        f"  per task            ${spent / len(measured):.3f}"
        if measured else "  per task            -",
        # The one that matters. A harness that fails cheaply looks good on the
        # line above and bad on this one.
        f"  per SOLVED task     ${spent / len(solved):.3f}"
        if solved else "  per SOLVED task     - (nothing solved)",
        f"  cache hit rate      {(read / went_in if went_in else 0):.0%}"
        "   (input served at a tenth of the price)",
        "",
        f"  {PRICED}, per million tokens: "
        + ", ".join(f"{k} ${v}" for k, v in PRICES.items()),
    ]
    if len(measured) < len(tasks):
        out.append(
            f"  {len(tasks) - len(measured)} task(s) never reached aven and are "
            "counted in accuracy but not in the averages"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "run",
        nargs="?",
        type=Path,
        help="a runs/<id> directory; the newest one by default",
    )
    args = parser.parse_args(argv)

    where = args.run
    if where is None:
        runs = sorted(
            (p for p in Path("runs").glob("*") if (p / "results.json").is_file()),
            key=lambda p: p.name,
        )
        if not runs:
            print("no finished run under runs/", file=sys.stderr)
            return 1
        where = runs[-1]

    try:
        tasks = read_run(where)
    except Unfinished as why:
        print(why, file=sys.stderr)
        return 1

    print(f"{where}\n")
    print(report(tasks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
