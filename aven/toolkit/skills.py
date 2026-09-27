"""Loading a skill, as one read-only tool.

The catalogue in the system prompt names every skill and says when it applies.
This is how the model gets the rest, once it has decided one applies.
"""

from __future__ import annotations

from typing import Annotated

from aven.harness import skills
from aven.harness.tools import Tool, tool


def skill_tools(available: list[skills.Skill]) -> list[Tool]:
    """One tool, or none at all when there are no skills to load."""
    if not available:
        return []

    by_name = {s.name: s for s in available}

    @tool(risk="read", preview="读技能 {name}")
    def load_skill(
        name: Annotated[str, "The skill's name, as the catalogue spells it"],
        file: Annotated[
            str, "A file bundled with the skill, if its instructions ask for one"
        ] = "",
    ) -> str:
        """Read a skill's full instructions, or one of the files beside them."""
        skill = by_name.get(name)
        if skill is None:
            return f"no skill called {name!r}. Available: {', '.join(by_name)}"

        text = skills.read(skill, file or None)
        if file:
            return text

        extras = skills.bundled(skill)
        if extras:
            text += "\n\nFiles bundled with this skill, readable with load_skill(" + (
                f"{name!r}, file=...): " + ", ".join(extras)
            )
        return text

    return [load_skill]
