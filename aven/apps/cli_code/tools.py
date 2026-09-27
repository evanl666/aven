"""Tools for working in a codebase: a shell, a search, and a listing.

The shell is the interesting one, because it breaks the assumption the whole
risk taxonomy rests on. Every other tool in aven knows what it does before it is
called: write_file writes, list_dir reads. A shell does whatever the string says,
and there is no honest constant to declare:

  "read"          a lie the tray cannot catch. `rm -rf` would run unannounced.
  "reversible"    a worse lie. There is no undo for an arbitrary command.
  "irreversible"  true, and unusable. Every `ls` would wait for a keypress,
                  which is how people learn to hold the approve key down.

So `run_command` decides per call, from the command it was handed - see
`Tool.risk_for`. A command the allowlist recognises as read-only is `read` and
runs; anything else is `irreversible`, which means it is staged and waits. The
policy layer keeps its invariant either way: it may still only raise what comes
out of here.

The judgement is a heuristic and errs toward asking. It is not a sandbox, and it
is not trying to be: a determined command can defeat any allowlist, and the
thing standing between a command and the disk here is the person reading the
preview. What the allowlist buys is that reading a file and listing a directory
do not cost them a keypress.

The obvious next step, not built: remembering that a person approved
`pytest -q` in this project, so the second one does not ask either. That needs
approvals to outlive a run, which nothing here does yet.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

from aven.harness.tools import Risk, Tool, ToolResult, tool
from aven.text import t

# Commands that cannot change anything, whatever arguments they are given. Kept
# short on purpose: every entry is a promise, and one wrong entry is a command
# running without being seen.
#
# Deliberately absent: `sed` and `awk`, which write with -i and with `print >`;
# `xargs`, which runs whatever it is piped; `sh`, `bash`, `python`, `node`, which
# run anything at all. Their being absent costs a keypress and buys the promise.
READING = frozenset(
    """
    ls cat head tail wc nl file stat du df tree
    grep rg ag fd find
    pwd basename dirname realpath readlink
    which type command whoami id hostname uname date env printenv
    sort uniq cut tr comm column diff
    jq
    """.split()
)

# git is two words before it means anything, so it gets its own list.
READING_GIT = frozenset(
    """
    status log diff show branch blame describe shortlog
    ls-files ls-tree cat-file rev-parse remote tag stash
    """.split()
)

# Flags that turn a reading command into a writing one. Not "-i": that is
# sed's in-place flag, and sed is not on the list at all, while `grep -i` is
# every other day.
WRITING_FLAGS = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint")

# Anything that sends output somewhere, or runs a second command whose name we
# never see. Backticks and $( ) can hide any command at all inside a `cat`.
HIDING = re.compile(r"[>`]|\$\(")

SPLIT = re.compile(r"\|\||&&|[;|\n&]")

# Long enough to be useful, short enough not to bury the conversation. A command
# whose output matters more than this should be written to a file and read.
OUTPUT = 20_000

TIMEOUT = 120


def read_only(command: str, extra: frozenset[str] = frozenset()) -> bool:
    """Whether this command certainly changes nothing.

    Certainly, not probably. Every path out of here that returns True is a
    promise that the command runs without anybody being asked, so anything not
    understood returns False - an unparseable command, an empty one, a name with
    a slash in it, a flag that is not recognised.
    """
    if HIDING.search(command):
        return False

    seen = False
    for piece in SPLIT.split(command):
        piece = piece.strip()
        if not piece:
            continue
        seen = True
        try:
            words = shlex.split(piece)
        except ValueError:
            # Unbalanced quotes. We do not know what this is.
            return False
        if not words:
            return False

        # Matched whole, against the first word exactly. That one rule covers
        # more than it looks like: `/bin/ls`, `./configure`, `-x`, `FOO=1 rm`
        # and `ls/x` are all simply not names on the list. Checking for a slash
        # or an "=" separately would read like extra protection and add none -
        # every case it catches, this already refused.
        name = words[0]

        if name == "git":
            # Skip flags and their values: `git -c color.ui=false status` is a
            # reading command, and taking "color.ui=false" for the subcommand
            # would have it wait for a keypress. A word with "=" in it is never
            # a subcommand, so this cannot let one through - `git -c x=y commit`
            # still finds "commit".
            rest = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
            if not rest or rest[0] not in READING_GIT:
                return False
        elif name in extra:
            pass
        elif name not in READING:
            return False

        if any(flag in words[1:] for flag in WRITING_FLAGS):
            return False

    # An empty command understood nothing, so it promises nothing.
    return seen


def code_tools(root: Path, *, allow: frozenset[str] = frozenset()) -> list[Tool]:
    """Build the coding toolset bound to one directory.

    `allow` adds names to the read-only allowlist for this run, which is how a
    project says that its own test command changes nothing worth confirming.
    """
    root = Path(root).expanduser().resolve()

    def judge(command: str) -> Risk:
        return "read" if read_only(command, allow) else "irreversible"

    @tool(risk=judge, preview=lambda command, **_: t("code.run", command=command))
    def run_command(
        command: Annotated[str, "A shell command, run in the project root"],
    ) -> ToolResult:
        """Run a shell command in the project.

        Commands that only read run straight away. Anything else is held for the
        person to confirm, so prefer reading with grep, glob and read_file over
        shelling out to do the same thing.
        """
        finished = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        return ToolResult(output=_report(finished))

    @tool(risk="read", preview=lambda pattern, **_: t("code.grep", pattern=pattern))
    def grep(
        pattern: Annotated[str, "A regular expression"],
        path: Annotated[str, "Where to search, relative to the project root"] = ".",
        glob: Annotated[str, "Only search files matching this, e.g. '*.py'"] = "",
    ) -> str:
        """Search the project's files for a pattern.

        Reports the file and line of each match. Prefer this over `grep` through
        run_command: it is bounded, it skips what should not be searched, and it
        needs nobody's approval.
        """
        target = _inside(root, path)
        matches: list[str] = []
        try:
            expression = re.compile(pattern)
        except re.error as broken:
            return f"that is not a valid regular expression: {broken}"

        for file in _walk(target, glob):
            try:
                for number, line in enumerate(
                    file.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if expression.search(line):
                        matches.append(f"{file.relative_to(root)}:{number}: {line.strip()[:160]}")
                        if len(matches) >= 200:
                            matches.append("... more matches, not shown")
                            return "\n".join(matches)
            except OSError:
                continue

        return "\n".join(matches) if matches else f"no match for {pattern!r}"

    @tool(risk="read", preview=lambda pattern, **_: t("code.glob", pattern=pattern))
    def glob(
        pattern: Annotated[str, "A path pattern, e.g. 'src/**/*.py'"],
    ) -> str:
        """List the files matching a pattern, most recently changed first."""
        found = sorted(
            (p for p in root.glob(pattern) if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not found:
            return f"nothing matches {pattern!r}"
        listed = [str(p.relative_to(root)) for p in found[:200]]
        if len(found) > 200:
            listed.append(f"... and {len(found) - 200} more")
        return "\n".join(listed)

    return [run_command, grep, glob]


class Outside(Exception):
    """The path is not inside the project."""


def _inside(root: Path, raw: str) -> Path:
    """The same boundary the file tools keep, for the same reason."""
    if raw.startswith("~"):
        raise Outside(f"{raw!r} is outside {root}")
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise Outside(f"{raw!r} is outside {root}")
    return target


# Folders whose contents are never what somebody is looking for, and are large
# enough to make a search useless if they are included.
SKIP = frozenset(
    ".git .hg .svn node_modules __pycache__ .venv venv .mypy_cache .pytest_cache "
    "dist build .tox .ruff_cache target".split()
)


def _walk(target: Path, pattern: str):
    """Every file under `target`, skipping what nobody meant to search."""
    if target.is_file():
        yield target
        return

    for path in target.rglob(pattern or "*"):
        if not path.is_file():
            continue
        if SKIP & set(path.parts):
            continue
        yield path


def _report(finished: subprocess.CompletedProcess[str]) -> str:
    """What the model is told, including the exit code when it is not zero.

    The code matters more than the output does: a test run that failed is the
    single most useful fact in the reply, and a model reading only stdout can
    miss it entirely.
    """
    parts = []
    if finished.stdout.strip():
        parts.append(finished.stdout.strip())
    if finished.stderr.strip():
        parts.append(f"[stderr]\n{finished.stderr.strip()}")
    if finished.returncode != 0:
        parts.append(f"[exited {finished.returncode}]")

    text = "\n".join(parts) if parts else "(no output)"
    if len(text) > OUTPUT:
        text = text[:OUTPUT] + f"\n... clipped, {len(text) - OUTPUT} more characters"
    return text
