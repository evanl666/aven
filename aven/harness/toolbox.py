"""Which tools are in play right now.

Every tool's schema rides in every request, ahead of the system prompt and the
conversation. Fourteen of them measured at about 1200 tokens a turn, most of
it for tools a given task never touches - the calendar is dead weight while
sorting a download folder.

So the box holds them all and offers a few. The rest are named in one line each
and brought in by `use_tools` when the task turns out to need them, the same
two-stage trade skills make.

The cost is real and worth stating: the tool list sits at the head of the
cached prefix, so bringing a group in invalidates the cache for that turn.
Groups are therefore coarse and few, and a group comes in once.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated

from aven.harness.tools import Tool, tool

ToolSource = Sequence[Tool] | Callable[[], Sequence[Tool]]


def resolve(source: ToolSource | None) -> list[Tool]:
    """Read a tool list that may still be deciding what it contains."""
    if source is None:
        return []
    return list(source() if callable(source) else source)


class ToolBox:
    """Tools that are always there, plus groups that can be brought in."""

    def __init__(self, core: Sequence[Tool], groups: dict[str, Sequence[Tool]]) -> None:
        self.core = list(core)
        self.groups = {name: list(tools) for name, tools in groups.items() if tools}
        self.brought_in: set[str] = set()

    def active(self) -> list[Tool]:
        """What the model may call this turn."""
        tools = list(self.core)
        for name in self.groups:
            if name in self.brought_in:
                tools += self.groups[name]
        if self.dormant():
            tools.append(self._opener())
        return tools

    def dormant(self) -> list[str]:
        return [name for name in self.groups if name not in self.brought_in]

    def bring_in(self, name: str) -> bool:
        if name not in self.groups or name in self.brought_in:
            return False
        self.brought_in.add(name)
        return True

    def catalogue(self, describe: dict[str, str]) -> str:
        """The line in the system prompt naming what is not loaded yet."""
        waiting = self.dormant()
        if not waiting:
            return ""
        listed = "\n".join(f"- {name}:{describe.get(name, '')}" for name in waiting)
        return (
            "还有这些工具没有加载。需要的时候用 use_tools 把整组拿进来,"
            "一次拿一组:\n\n" + listed
        )

    def _opener(self) -> Tool:
        @tool(risk="read", preview="加载 {group} 这组工具")
        def use_tools(
            group: Annotated[str, "The group to bring in, as the catalogue names it"],
        ) -> str:
            """Bring a group of tools into play, when the task turns out to need it."""
            if self.bring_in(group):
                names = ", ".join(t.name for t in self.groups[group])
                return f"loaded {group}: {names}"

            if group in self.groups:
                return f"{group} is already loaded"
            return f"no group called {group!r}. Waiting: {', '.join(self.dormant()) or 'none'}"

        return use_tools
