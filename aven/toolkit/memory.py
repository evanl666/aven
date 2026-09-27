"""Remembering and forgetting, as two ordinary tools.

The model decides what is worth keeping. That is cheaper and more predictable
than a second pass over every turn asking a model what it noticed, and it puts
the decision where the judgement already is: it has just been told the fact and
knows whether it will need it again.

Both tools are reversible, so remembering the wrong thing costs one keystroke,
the same as moving the wrong file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from aven.harness import memory
from aven.harness.tools import Tool, ToolResult, tool

Scope = Literal["here", "global"]

WHERE = {"global": "(所有地方)", "here": "(这个目录)"}

HERE = "AVEN.md"
GLOBAL = (".aven", "AVEN.md")


def memory_tools(root: Path) -> list[Tool]:
    """Build the memory tools for one working directory."""
    root = Path(root).expanduser().resolve()

    def file_for(scope: str) -> Path:
        # `global` is the one path in aven that is written outside --root. It is
        # a fixed location aven owns, not a path the model chose, so the sandbox
        # it bypasses is not the sandbox that matters.
        #
        # Resolved on each call rather than at import: a module-level
        # Path.home() is frozen the moment aven is imported, which no test can
        # redirect - and a test that cannot redirect it writes to the real one.
        if scope == "global":
            return Path.home().joinpath(*GLOBAL)
        return root / HERE

    @tool(risk="reversible",
          preview=lambda fact, scope="here", **_: f"记住{WHERE.get(scope, WHERE['here'])}:{fact}")
    def remember(
        fact: Annotated[str, "One fact, in the person's own words where possible"],
        scope: Annotated[
            str,
            "'global' for something true wherever they work - a person, an "
            "address, a preference. 'here' for something about this folder.",
        ] = "here",
    ) -> ToolResult:
        """Write down something worth knowing next time.

        For facts that outlast the task: who someone is, an address, a folder
        convention, a preference. Not for what is happening right now - the
        conversation already holds that.
        """
        path = file_for(scope)
        before = memory.add(path, fact.strip())

        if before is None:
            return ToolResult(output=f"already remembered: {fact}")

        # The undo restores the whole file, as write_file's does. Rolling these
        # back out of order would lose whatever was remembered after - which
        # the tray never does, since it always unwinds newest first.
        return ToolResult(
            output=f"remembered in {_show(path)}: {fact}",
            undo=lambda: path.write_text(before, encoding="utf-8"),
        )

    @tool(risk="reversible",
          preview=lambda fact, scope="here", **_: f"忘掉{WHERE.get(scope, WHERE['here'])}:{fact}")
    def forget(
        fact: Annotated[str, "The fact to remove; close wording is enough"],
        scope: Annotated[str, "Which file it was remembered in"] = "here",
    ) -> ToolResult:
        """Drop something previously remembered, because it changed or was wrong."""
        path = file_for(scope)
        removed = memory.drop(path, fact.strip())

        if removed is None:
            return ToolResult(output=f"nothing remembered like that in {_show(path)}")

        before, gone = removed
        return ToolResult(
            output=f"forgot: {gone}",
            undo=lambda: path.write_text(before, encoding="utf-8"),
        )

    return [remember, forget]


def _show(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
