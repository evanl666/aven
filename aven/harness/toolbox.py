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
from aven.text import t

ToolSource = Sequence[Tool] | Callable[[], Sequence[Tool]]


def resolve(source: ToolSource | None) -> list[Tool]:
    """Read a tool list that may still be deciding what it contains."""
    if source is None:
        return []
    return list(source() if callable(source) else source)


class ToolBox:
    """Tools that are always there, plus groups that can be brought in.

    A group may be a function rather than a list. That is what lets a connector
    whose tools need a token build them at the moment it is brought in: holding
    them from startup would mean either building tools against a credential that
    does not exist yet, or asking for the credential before anybody said they
    wanted the service.

    `gate` is how something outside gets a veto. The box knows which groups
    exist; it does not know that one of them needs signing in to first, and it
    should not have to. A gate returning a sentence refuses the group and says
    why, in words meant for the model to act on.
    """

    def __init__(
        self,
        core: Sequence[Tool],
        groups: dict[str, ToolSource],
        gate: Callable[[str], str | None] | None = None,
    ) -> None:
        self.core = list(core)
        # An empty list is a group with nothing in it and is dropped. A function
        # is kept unasked - calling it here to find out whether it is empty is
        # exactly the work being deferred.
        self.groups = {
            name: tools
            for name, tools in groups.items()
            if callable(tools) or len(tools) > 0
        }
        self.gate = gate
        self.brought_in: set[str] = set()

    def tools_in(self, name: str) -> list[Tool]:
        """One group's tools, built now if they were waiting to be."""
        return resolve(self.groups.get(name))

    def active(self) -> list[Tool]:
        """What the model may call this turn."""
        tools = list(self.core)
        for name in self.groups:
            if name in self.brought_in:
                tools += self.tools_in(name)
        if self.dormant():
            tools.append(self._opener())
        return tools

    def dormant(self) -> list[str]:
        return [name for name in self.groups if name not in self.brought_in]

    def refused(self, name: str) -> str | None:
        """Why this group may not be brought in, if it may not."""
        return self.gate(name) if self.gate else None

    def bring_in(self, name: str) -> bool:
        if name not in self.groups or name in self.brought_in:
            return False
        if self.refused(name):
            return False
        self.brought_in.add(name)
        return True

    def put_away(self, name: str) -> bool:
        """Take a group back out of play.

        The conversation keeps whatever was already said with those tools. It
        has to: the transcript is a record of what happened, and editing out the
        calls would make the model's own earlier reasoning refer to nothing.
        What changes is only what it may call from here.
        """
        if name not in self.brought_in:
            return False
        self.brought_in.discard(name)
        return True

    def catalogue(self, describe: dict[str, str]) -> str:
        """The line in the system prompt naming what is not loaded yet."""
        waiting = self.dormant()
        if not waiting:
            return ""
        listed = "\n".join(f"- {name}:{describe.get(name, '')}" for name in waiting)
        return (
            "These tools are not loaded yet. When the task turns out to need "
            "one, use use_tools to bring in its whole group, one group at a "
            "time:\n\n" + listed
        )

    def _opener(self) -> Tool:
        @tool(risk="read", preview=lambda group, **_: t("toolbox.load", group=group))
        def use_tools(
            group: Annotated[str, "The group to bring in, as the catalogue names it"],
        ) -> str:
            """Bring a group of tools into play, when the task turns out to need it."""
            # Asked before the attempt, so a refusal can explain itself. Without
            # this the model gets "already loaded" for a group that is actually
            # locked, and spends its next turn calling tools that are not there.
            refused = self.refused(group) if group in self.groups else None
            if refused:
                return refused

            if self.bring_in(group):
                names = ", ".join(t.name for t in self.tools_in(group))
                return f"loaded {group}: {names}"

            if group in self.groups:
                return f"{group} is already loaded"
            return f"no group called {group!r}. Waiting: {', '.join(self.dormant()) or 'none'}"

        return use_tools
