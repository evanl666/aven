"""Skills: instructions that are only paid for when they are needed.

A skill is a folder with a SKILL.md and whatever it needs beside it - scripts,
references, templates. Following the Agent Skills specification, so a folder
written for another agent works here unchanged.

What makes them worth having is two-stage loading. Every skill's name and
description sit in the system prompt, which costs a line each; the instructions
themselves are fetched only when the model decides a task calls for them. Ten
skills therefore cost ten lines, not ten documents.

That puts the weight on the description. It is the only thing the model sees
when deciding, so it has to say both what the skill does and when it applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ENTRY = "SKILL.md"
FOLDER = "skills"
MAX_CHARS = 40_000

NO_DESCRIPTION = "(no description - the model cannot tell when to use this)"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    home: Path

    @property
    def entry(self) -> Path:
        return self.home / ENTRY


def parse(text: str, fallback: str) -> tuple[str, str]:
    """Pull name and description out of the YAML frontmatter.

    Read by hand rather than with a YAML parser: the two fields that matter are
    plain strings on their own lines, and a dependency that can execute what it
    loads is a poor trade for that.
    """
    name, description = fallback, NO_DESCRIPTION

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return name, description

    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip().strip("\"'")
        if key.strip() == "name" and value:
            name = value
        elif key.strip() == "description" and value:
            description = value

    return name, description


def find(root: Path) -> list[Skill]:
    """Skills available here: the person's own, then this folder's.

    Later wins on a name clash, so a project can replace a global skill with
    its own version of the same idea.
    """
    root = Path(root).expanduser().resolve()
    homes = [Path.home() / ".aven" / FOLDER, root / ".aven" / FOLDER]

    by_name: dict[str, Skill] = {}
    for home in homes:
        if not home.is_dir():
            continue
        for folder in sorted(p for p in home.iterdir() if (p / ENTRY).is_file()):
            try:
                text = (folder / ENTRY).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, description = parse(text, folder.name)
            by_name[name] = Skill(name=name, description=description, home=folder)

    return list(by_name.values())


def catalogue(skills: list[Skill]) -> str:
    """The part that rides in every request: one line each, no instructions."""
    if not skills:
        return ""

    listed = "\n".join(f"- {s.name}:{s.description}" for s in skills)
    return (
        "你有这些技能可以调用。只有名字和说明在这里,觉得用得上时"
        "用 load_skill 取完整内容:\n\n" + listed
    )


def read(skill: Skill, relative: str | None = None) -> str:
    """The skill's instructions, or one of the files bundled with it."""
    target = skill.entry if relative is None else (skill.home / relative).resolve()

    # A skill may live outside --root, so its own folder is the boundary here.
    if target != skill.home and skill.home not in target.parents:
        raise Outside(f"{relative!r} is outside the {skill.name} skill")
    if not target.is_file():
        raise Outside(f"{relative or ENTRY!r} is not a file in the {skill.name} skill")

    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
    return text


def bundled(skill: Skill) -> list[str]:
    """Files beside SKILL.md, so the instructions can point at them by name."""
    return sorted(
        str(p.relative_to(skill.home))
        for p in skill.home.rglob("*")
        if p.is_file() and p.name != ENTRY
    )


class Outside(Exception):
    """A path that is not inside the skill it claims to belong to."""
