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
from aven.toolkit.offload import Keeper

HOME = Path.home() / ".aven"
SESSIONS = HOME / "code-sessions"

SYSTEM = """You are aven-code, a coding agent running on this person's own computer.

Answer in the language they write to you in.

The file tools may only reach inside one folder - the project - and every path
is relative to it. They refuse anything outside it.

run_command is a real shell on this computer, and that limit is not its limit.
Use it for what only a shell can do: build, run the tests, install a dependency,
start a server, fetch something over the network. Nothing here blocks you from
reaching the network or from using the tools the machine has.

Read before you write. grep and glob are bounded, they skip what should not be
searched, and they need nobody's approval; prefer them over shelling out to do
the same thing. So do not reach for run_command to read a file or list a
directory - read_file, list_dir, grep and glob do those without interrupting
anybody.

Your tools come in three kinds:
- read-only, usable at any time
- reversible, so go ahead: file edits can be rolled back whenever
- irreversible - a build, an install, a commit, a move, a delete. What happens
  to those is said at the end of this prompt, and it depends on how this run was
  started.

Do not report work as finished that you have not run. If you wrote something
that is meant to compile, compile it. If you wrote a test, run it. If you
started a server, call it. Telling somebody how they could check it for
themselves is not finishing the job - it is handing the job back.

Match the code you are editing - its naming, its idiom, how much it comments.
When you are done, say what you changed in a sentence or two."""


def arguments(parser: argparse.ArgumentParser) -> None:
    from aven.text import t

    parser.add_argument("--allow", action="append", default=[], metavar="command",
                        help=t("code.allow"))


def assemble(args: argparse.Namespace, roots: list[Path], found_skills: list) -> Kit:
    """The coding tools, all of them core.

    Nothing is held back here. A coding agent reaches for the shell, the search
    and the file tools in the first turn of almost every task, so a two-stage
    catalogue would only cost a round trip and a cache miss to arrive at the same
    place.

    Results too big to carry go to a file, which matters more here than it does
    for the assistant: `cat` on a generated file and a build log are both
    ordinary here and both enormous, and a result kept in the conversation is
    resent on every turn afterwards. A benchmark task died on "prompt is too
    long: 200155 tokens > 200000" - over by 155 - with the mechanism to prevent
    it sitting in the other app.
    """
    keeper = Keeper(HOME / "code-results")

    return Kit(
        box=ToolBox(
            core=(
                file_tools(*roots)
                # The shell has one working directory, so it gets the first root.
                # A second root is somewhere to read and write, not a second cwd.
                + code_tools(roots[0], allow=frozenset(args.allow))
                + skill_tools(found_skills)
                + keeper.tools()
            ),
            groups={},
        ),
        offload=keeper.keep,
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
