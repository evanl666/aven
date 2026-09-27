"""The aven-code command: a coding agent on the same harness.

    aven-code "where is this function called from"
    aven-code -c                                   continuing the last session
    git diff | aven-code -p "review this"          answer on stdout, then exit

What it shares with the assistant is everything except this file and tools.py:
the loop, the session tree, the staging tray, the compaction, the steering queue,
the full-screen shell, the review step, and every interface from --tree to
--mode json. What differs is the prompt and what it can reach.

Sessions live apart from the assistant's, so `-c` here never lands in the middle
of a conversation about somebody's calendar.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from aven.apps.cli_code.tools import code_tools
from aven.harness.toolbox import ToolBox
from aven.terminal.app import Blueprint, Kit, launch
from aven.toolkit import file_tools, skill_tools

SESSIONS = Path.home() / ".aven" / "code-sessions"

SYSTEM = """You are aven-code, a coding agent running on this person's own computer.

Answer in the language they write to you in.

You may only act inside one folder - the project - and every path is relative to
it. The tools refuse anything outside it.

Read before you write. grep and glob are bounded, they skip what should not be
searched, and they need nobody's approval; prefer them over shelling out to do
the same thing.

Your tools come in three kinds:
- read-only, usable at any time
- reversible, so go ahead: file edits can be rolled back whenever
- irreversible, which do not happen now - they join a queue and wait to be
  confirmed

run_command is the one tool whose kind depends on what you pass it. A command
that only reads runs immediately. Anything that could change something - a build,
an install, a commit, a move, a delete - is held for this person to confirm, and
its result will say "staged". "staged" means IT HAS NOT RUN. Do not treat its
effects as real and do not keep reasoning as though they were: finish what you
can, then say what is waiting.

So do not reach for run_command to read a file or list a directory. read_file,
list_dir, grep and glob do those without interrupting anybody.

Match the code you are editing - its naming, its idiom, how much it comments.
When you are done, say what you changed in a sentence or two."""


def arguments(parser: argparse.ArgumentParser) -> None:
    from aven.text import t

    parser.add_argument("--allow", action="append", default=[], metavar="command",
                        help=t("code.allow"))


def assemble(args: argparse.Namespace, root: Path, found_skills: list) -> Kit:
    """The coding tools, all of them core.

    Nothing is held back here. A coding agent reaches for the shell, the search
    and the file tools in the first turn of almost every task, so a two-stage
    catalogue would only cost a round trip and a cache miss to arrive at the same
    place.
    """
    return Kit(
        box=ToolBox(
            core=(
                file_tools(root)
                + code_tools(root, allow=frozenset(args.allow))
                + skill_tools(found_skills)
            ),
            groups={},
        )
    )


def blueprint() -> Blueprint:
    return Blueprint(
        program="aven-code",
        description="code.description",
        banner="code.banner",
        system=SYSTEM,
        sessions=SESSIONS,
        assemble=assemble,
        arguments=arguments,
    )


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(launch(blueprint(), argv))


if __name__ == "__main__":
    raise SystemExit(main())
