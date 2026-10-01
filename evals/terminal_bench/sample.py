"""Pick a reproducible slice of Terminal-Bench, stratified by difficulty.

Running all 80 tasks costs real money and several hours. Running a handful
costs neither, and is worth nothing at all if the handful was chosen after
seeing which ones pass.

So the choice is made here, by a rule, from the task metadata alone:

- the sample keeps the difficulty mix of the whole set, because a score is
  meaningless without knowing what it was a score of, and "I picked thirty"
  can quietly mean "I picked thirty easy ones"
- within a difficulty the tasks are sorted by name and taken at even spacing,
  so the same n always gives the same tasks and nothing about the result can
  feed back into the selection
- no seed, no shuffle. A seed is one more thing somebody can try several of.

    python evals/terminal_bench/sample.py 30          # the task ids
    python evals/terminal_bench/sample.py 30 --args   # as -t flags for tb

The honest reading of a sampled score: it estimates what the full set would
say, with the error you get from thirty draws rather than eighty. It is not
comparable to a published 80-task number, and this file is what lets somebody
check that the thirty were not chosen to flatter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Where `tb` unpacks a dataset. Version-pinned on purpose: the task list is
# part of what a score means, and 0.1.1 is what the numbers in the README were
# measured against.
TASKS = Path.home() / ".cache/terminal-bench/terminal-bench-core/0.1.1"

ORDER = ("easy", "medium", "hard")


def difficulty_of(task: Path) -> str:
    """What the task says about itself, or "medium" if it says nothing.

    Unlabelled sorts with the middle rather than being dropped: a task missing
    a line of metadata is still a task, and dropping it would silently change
    the mix this file exists to preserve.
    """
    try:
        for line in (task / "task.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("difficulty:"):
                return line.split(":", 1)[1].strip() or "medium"
    except OSError:
        pass
    return "medium"


def groups(where: Path = TASKS) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in ORDER}
    for task in sorted(where.iterdir()):
        if not (task / "task.yaml").is_file():
            continue
        found.setdefault(difficulty_of(task), []).append(task.name)
    return {name: sorted(names) for name, names in found.items() if names}


def spread(names: list[str], take: int) -> list[str]:
    """`take` of these, evenly spaced through the sorted list.

    Evenly spaced rather than the first n: the names are alphabetical, and the
    first n of an alphabetical list is a sample of one letter range. Several
    of these tasks come in families - pytorch-model-cli, pytorch-model-cli.easy,
    pytorch-model-cli.hard - and taking a prefix would take whole families and
    miss whole others.
    """
    if take >= len(names):
        return list(names)
    if take <= 0:
        return []
    step = len(names) / take
    return [names[int(n * step)] for n in range(take)]


def sample(n: int, where: Path = TASKS) -> list[str]:
    """`n` tasks, keeping the difficulty mix of the whole set."""
    by_difficulty = groups(where)
    total = sum(len(names) for names in by_difficulty.values())
    if total == 0:
        return []
    n = min(n, total)

    # Largest-remainder, so the parts sum to n exactly rather than to n-2
    # after three independent roundings.
    exact = {d: len(names) * n / total for d, names in by_difficulty.items()}
    take = {d: int(share) for d, share in exact.items()}
    short = n - sum(take.values())
    for d in sorted(exact, key=lambda d: (-(exact[d] - take[d]), d))[:short]:
        take[d] += 1

    chosen: list[str] = []
    for d in [*ORDER, *(d for d in by_difficulty if d not in ORDER)]:
        if d in by_difficulty:
            chosen += spread(by_difficulty[d], take.get(d, 0))
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("n", type=int, help="how many tasks")
    parser.add_argument(
        "--args", action="store_true", help="print as -t flags for tb run"
    )
    parser.add_argument("--tasks", type=Path, default=TASKS)
    args = parser.parse_args(argv)

    if not args.tasks.is_dir():
        print(
            f"no dataset at {args.tasks} - run tb once to unpack it",
            file=sys.stderr,
        )
        return 1

    chosen = sample(args.n, args.tasks)
    if args.args:
        print(" ".join(f"-t {name}" for name in chosen))
    else:
        mix = groups(args.tasks)
        for d in [*ORDER, *(k for k in mix if k not in ORDER)]:
            here = [name for name in chosen if name in set(mix.get(d, ()))]
            if here:
                print(f"{d} ({len(here)} of {len(mix[d])}):")
                for name in here:
                    print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
