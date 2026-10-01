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

# Per million tokens, in dollars. An assumption, not a measurement - the run
# does not record what it was charged, so this table is the one thing here that
# can be out of date without anything failing. Printed with the results for
# that reason, and overridable, so a number from this script can always be
# checked against what the invoice actually said.
PRICES = {
    "input": 3.00,
    "output": 15.00,
    "cache_write": 3.75,  # 1.25x input
    "cache_read": 0.30,  # 0.1x input
}

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


def report(tasks: list[Task]) -> str:
    rows = sorted(tasks, key=lambda x: (-x.cost, x.name))
    measured = [x for x in tasks if x.measured]
    solved = [x for x in tasks if x.resolved]

    out = [
        f"{'task':<34}{'':<4}{'req':>5}{'input':>10}{'cached':>10}"
        f"{'out':>8}{'hit':>7}{'$':>9}",
        "-" * 87,
    ]
    for task in rows:
        mark = "ok " if task.resolved else "   "
        if not task.measured:
            out.append(f"{task.name:<34}{mark:<4}{'- never ran -':>49}")
            continue
        out.append(
            f"{task.name:<34}{mark:<4}{task.requests:>5}{task.total_input:>10,}"
            f"{task.cache_read:>10,}{task.output:>8,}"
            f"{task.hit_rate:>6.0%}{task.cost:>9.3f}"
        )

    spent = sum(x.cost for x in tasks)
    read = sum(x.cache_read for x in tasks)
    went_in = sum(x.total_input for x in tasks)

    out += [
        "-" * 87,
        f"{'total':<34}{'':<4}{sum(x.requests for x in tasks):>5}{went_in:>10,}"
        f"{read:>10,}{sum(x.output for x in tasks):>8,}"
        f"{(read / went_in if went_in else 0):>6.0%}{spent:>9.2f}",
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
        "  prices assumed, per million tokens: "
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
