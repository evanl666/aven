"""Standing instructions the person leaves for aven.

A system prompt compiled into the program can say how an assistant behaves in
general. It cannot know that invoices belong in 報銷/YYYY-MM, that finance is
finance@corp.com, or that this person wants short emails. Those are facts about
one person and one folder, and asking for them again every session is the
difference between a tool and an assistant.

So aven reads them from files the person owns and can edit: one global, plus
any found walking up from the working directory. They are concatenated
outermost first, because the most specific instruction should be the last thing
the model reads.

This is the cheap half of memory - the person writes it. The expensive half,
where aven notices something worth keeping and writes it down itself, builds on
the same files.
"""

from __future__ import annotations

from pathlib import Path

# AGENTS.md is read too, so a folder already set up for another agent works
# here without being duplicated.
NAMES = ("AVEN.md", "AGENTS.md")

MAX_CHARS = 20_000  # per file: instructions, not a knowledge base


def find(root: Path) -> list[Path]:
    """Instruction files that apply to `root`, outermost first.

    The walk stops at the home directory. Above it are other people's folders
    and system directories, where a stray AGENTS.md was not written for this.
    """
    home = Path.home().resolve()
    root = Path(root).expanduser().resolve()

    found: list[Path] = []

    global_file = home / ".aven" / "AVEN.md"
    if global_file.is_file():
        found.append(global_file)

    walked: list[Path] = []
    for folder in [root, *root.parents]:
        for name in NAMES:
            candidate = folder / name
            if candidate.is_file() and candidate not in found:
                walked.append(candidate)
                break  # one file per folder: AVEN.md wins over AGENTS.md
        if folder == home:
            break

    # The walk goes inwards-out; the model should read it outwards-in.
    return found + list(reversed(walked))


def read(paths: list[Path]) -> str:
    """Render the files for the system prompt, each labelled with its source.

    The label is not decoration: when two files disagree, the model needs to
    see which one is the more specific, and the person needs to know which file
    to edit.
    """
    blocks = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        blocks.append(f'<instructions from="{_short(path)}">\n{text}\n</instructions>')

    return "\n\n".join(blocks)


def _short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
