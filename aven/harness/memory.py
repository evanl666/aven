"""What aven has been told to remember, kept in the person's own files.

The instruction files from `context.py` are already the right place: they are
plain Markdown, they load into every session, and the person owns them. So
remembering is appending a line to one of them rather than filling a store they
cannot read.

aven's own lines live below a marker, so the part a person wrote by hand is
never touched and stays at the top where they left it:

    - keep my emails short              <- theirs

    <!-- aven remembers -->
    - finance is finance@corp.com       <- aven's

The marker is an HTML comment, invisible wherever Markdown is rendered and
trivial to find. Everything below it is aven's to edit; everything above is not.
"""

from __future__ import annotations

from pathlib import Path

MARKER = "<!-- aven remembers -->"

# Enough for the facts a person's assistant needs; past this, recalling costs
# more than remembering saves, and the right move is to forget something.
LIMIT = 200


def split(text: str) -> tuple[str, list[str]]:
    """Separate what the person wrote from what aven appended."""
    head, marker, tail = text.partition(MARKER)
    if not marker:
        return text, []
    facts = [line[2:].strip() for line in tail.splitlines() if line.startswith("- ")]
    return head, facts


def render(head: str, facts: list[str]) -> str:
    """Put the two halves back together, aven's below the marker."""
    head = head.rstrip()
    if not facts:
        return head + "\n" if head else ""

    remembered = "\n".join(f"- {fact}" for fact in facts)
    return f"{head}\n\n{MARKER}\n{remembered}\n" if head else f"{MARKER}\n{remembered}\n"


def recall(path: Path) -> list[str]:
    """Everything aven has remembered in this file."""
    if not path.is_file():
        return []
    return split(path.read_text(encoding="utf-8"))[1]


def add(path: Path, fact: str) -> str | None:
    """Append a fact. Returns the file's previous contents, or None if it was
    already there.

    The previous contents are the undo: rewriting the whole file is exact,
    where removing a line again would have to find it a second time.
    """
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    head, facts = split(before)

    if fact in facts:
        return None
    if len(facts) >= LIMIT:
        raise Full(f"already remembering {len(facts)} things; forget one first")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(head, [*facts, fact]), encoding="utf-8")
    return before


def drop(path: Path, fact: str) -> tuple[str, str] | None:
    """Remove a fact, matched loosely. Returns (previous contents, what went).

    Loosely, because the model is recalling a line it saw summarised in a
    prompt and will rarely reproduce it to the character.
    """
    if not path.is_file():
        return None

    before = path.read_text(encoding="utf-8")
    head, facts = split(before)

    wanted = _loose(fact)
    match = next((f for f in facts if _loose(f) == wanted), None)
    if match is None:
        match = next((f for f in facts if wanted in _loose(f)), None)
    if match is None:
        return None

    path.write_text(render(head, [f for f in facts if f != match]), encoding="utf-8")
    return before, match


class Full(Exception):
    """The file is holding as many facts as it usefully can."""


def _loose(text: str) -> str:
    return "".join(text.split()).lower()
