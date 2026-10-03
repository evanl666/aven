"""Who is responsible for each failure: the harness, the environment, or the model.

The point of running a benchmark against your own harness is not the score. It
is this list. A score tells you how many tasks failed; this tells you which of
them you can do anything about.

    python evals/terminal_bench/blame.py                  # the newest run
    python evals/terminal_bench/blame.py runs/<id>
    python evals/terminal_bench/blame.py runs/<a> runs/<b>   # what moved

## Why this is a script and not a reading

Iterating "find the harness faults, fix them, run again" needs a stopping
condition, and "I looked at the logs and they seem fine now" is not one - it is
the person who made the changes grading their own work. Every signal below is a
string the harness itself emits, or a field Terminal-Bench records, so the
attribution is the same whoever runs it and whatever they were hoping for.

## The signals, and what each one means

Every entry here says "aven got in the way". None of them is a judgement about
whether the model was clever enough:

    install failed       the agent never ran at all
    stream dropped       the connection died mid-reply and was not retried
    context overflow     the conversation outgrew the window
    work left staged     an irreversible call waited for somebody who was absent
    sandbox refusal      a path the task needed was outside the roots
    hit the turn cap     the budget ran out with work in progress
    stalled              the loop noticed it was going in circles
    wall-clock timeout   the clock ran out
    no log at all        the container never produced one

A failure with none of these ran to the end, made its own choices, and got the
answer wrong. That is the model, and no amount of harness work will fix it.

## The one thing this cannot tell you

A wall-clock timeout is the ambiguous case and it is counted against the
harness on purpose. Sometimes the task genuinely needs more minutes than it
was given, which is nobody's fault; sometimes the harness wasted them. Counting
it against the harness means the list errs toward more work rather than less,
which is the right direction for a list whose purpose is to be driven to zero.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Each one is a thing the harness did, visible in what it wrote.
SIGNALS: dict[str, object] = {
    "install failed": lambda t, d: bool(re.search(r"^INSTALL_FAIL_STATUS$", t, re.M)),
    "stream dropped": lambda t, d: "RemoteProtocolError" in t,
    "context overflow": lambda t, d: "ContextOverflow" in t,
    "work left staged": lambda t, d: "staged, waiting" in t,
    "sandbox refusal": lambda t, d: "is outside" in t,
    "hit the turn cap": lambda t, d: "turn limit" in t,
    "stalled": lambda t, d: "repeating the same call" in t,
    "wall-clock timeout": lambda t, d: d.get("failure_mode") == "agent_timeout",
    "no log at all": lambda t, d: not t.strip(),
}


def output_of(run: Path, task: str) -> str:
    """Everything the agent wrote in that task, with the terminal stripped out."""
    found = sorted((run / task).rglob("agent.log")) if (run / task).is_dir() else []
    if not found:
        return ""
    return ANSI.sub("", found[-1].read_text(errors="replace")).replace("\r", "\n")


def requests_in(text: str) -> int | None:
    found = re.findall(r"(\d+) requests ·", text)
    return int(found[-1]) if found else None


def blame(run: Path) -> dict[str, dict]:
    """Every task in the run, with who is answerable for it."""
    out: dict[str, dict] = {}
    for f in sorted(run.glob("*/*/results.json")):
        d = json.loads(f.read_text())
        name = d["task_id"]
        text = output_of(run, name)
        solved = d.get("is_resolved") in (True, "True")
        # Only a failure has anything to answer for. A task that passed while
        # leaving a call staged is a curiosity, not a fault, and a list meant
        # to be driven to zero must not count curiosities - otherwise zero is
        # unreachable for reasons nobody needs to fix.
        why = (
            []
            if solved
            else [label for label, test in SIGNALS.items() if test(text, d)]
        )
        out[name] = {"solved": solved, "harness": why, "requests": requests_in(text)}
    return out


def report(run: Path) -> str:
    rows = blame(run)
    solved = [n for n, r in rows.items() if r["solved"]]
    faults = {n: r for n, r in rows.items() if not r["solved"] and r["harness"]}
    model = [n for n, r in rows.items() if not r["solved"] and not r["harness"]]
    asked = sum(r["requests"] or 0 for r in rows.values())

    lines = [f"{run.name}", ""]
    lines.append(f"  solved            {len(solved)}/{len(rows)}")
    lines.append(f"  model's failures  {len(model)}")
    lines.append(f"  HARNESS FAULTS    {len(faults)}" + ("   <- drive this to zero"
                                                         if faults else "   <- none left"))
    lines.append(f"  model requests    {asked:,}")

    if faults:
        lines += ["", "harness faults:"]
        for name, r in sorted(faults.items()):
            lines.append(f"  {name:<32} {', '.join(r['harness'])}")
    if model:
        lines += ["", "the model's, and not fixable here:"]
        for name in sorted(model):
            lines.append(f"  {name:<32} ran to the end, {rows[name]['requests']} requests")
    return "\n".join(lines)


def moved(before: Path, after: Path) -> str:
    """What changed between two runs, which is the only thing a round proves."""
    a, b = blame(before), blame(after)
    names = sorted(set(a) | set(b))

    def faults(rows):
        return {n for n, r in rows.items() if not r["solved"] and r["harness"]}

    lines = [f"{before.name}  ->  {after.name}", ""]
    lines.append(f"  solved          {sum(r['solved'] for r in a.values())}"
                 f" -> {sum(r['solved'] for r in b.values())}")
    lines.append(f"  harness faults  {len(faults(a))} -> {len(faults(b))}")
    lines.append(f"  requests        {sum(r['requests'] or 0 for r in a.values()):,}"
                 f" -> {sum(r['requests'] or 0 for r in b.values()):,}")

    gained = [n for n in names if b.get(n, {}).get("solved") and not a.get(n, {}).get("solved")]
    lost = [n for n in names if a.get(n, {}).get("solved") and not b.get(n, {}).get("solved")]
    if gained:
        lines += ["", "newly solved:"] + [f"  + {n}" for n in gained]
    if lost:
        lines += ["", "REGRESSED - was solved, now is not:"] + [f"  - {n}" for n in lost]

    fixed = faults(a) - faults(b)
    fresh = faults(b) - faults(a)
    if fixed:
        lines += ["", "harness faults gone:"] + [
            f"  + {n}  ({', '.join(a[n]['harness'])})" for n in sorted(fixed)
        ]
    if fresh:
        lines += ["", "harness faults NEW this round:"] + [
            f"  - {n}  ({', '.join(b[n]['harness'])})" for n in sorted(fresh)
        ]
    return "\n".join(lines)


def newest() -> Path | None:
    done = [p for p in Path("runs").glob("*") if (p / "results.json").is_file()]
    return max(done, key=lambda p: p.name) if done else None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2:
        print(moved(Path(args[0]), Path(args[1])))
        return 0
    run = Path(args[0]) if args else newest()
    if run is None or not (run / "results.json").is_file():
        print("no finished run to blame", file=sys.stderr)
        return 1
    print(report(run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
