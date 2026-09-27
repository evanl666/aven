"""File tools, scoped to one or more roots.

Every path the model supplies is resolved and checked against the roots before
anything happens. A model that has read a hostile web page will eventually ask
for ../../.ssh/id_rsa; the answer has to be no at the tool boundary, not in the
prompt, because the prompt is exactly what the attacker got to write.

Several roots, because a desktop assistant works across Downloads, Documents and
Desktop in one sentence - "file these invoices" spans two of them. The only way
to do that with a single root was to make the root the home directory, at which
point the sandbox stopped meaning anything.

Deletion moves to an aven-owned trash directory rather than unlinking, which is
what makes it reversible - and reversible work needs no approval.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Annotated

from aven.harness.tools import Diff, Tool, ToolResult, tool
from aven.text import t

MAX_READ = 40_000  # characters; enough for source and notes, not for a video


class Outside(Exception):
    """A path resolved to somewhere outside every root."""


class NotFound(Exception):
    """The passage to edit is not in the file."""


class Ambiguous(Exception):
    """The passage to edit appears more than once."""


def _current(path: Path | None) -> str:
    """What is in a file now, for a diff against what would replace it.

    Empty for a file that does not exist yet, which is the truthful "before" of
    creating one.
    """
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_READ]
    except OSError:
        return ""


def _clip(text: str, width: int = 40) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def names_for(roots: list[Path]) -> dict[str, Path]:
    """A short name per root, unique among them.

    The basename nearly always does. Two roots with the same basename get the
    parent folded in, because a name the model cannot tell apart is worse than a
    long one.
    """
    named: dict[str, Path] = {}
    for root in roots:
        name = root.name or str(root)
        if name in named:
            name = f"{root.parent.name}-{name}"
        while name in named:
            name = f"{name}-2"
        named[name] = root
    return named


def file_tools(*roots: Path) -> list[Tool]:
    """Build the file toolset bound to one or more directories.

    The first root is the working one. A bare relative path resolves against it,
    which is what a single-root run has always done and still does exactly.
    """
    if not roots:
        raise ValueError("file_tools needs at least one root")

    allowed = [Path(r).expanduser().resolve() for r in roots]
    root = allowed[0]
    named = names_for(allowed)
    trash = Path.home() / ".aven" / "trash"

    def inside_quietly(raw: str) -> Path | None:
        """`inside`, for a preview that must not raise.

        A detail is drawn before anything runs, including for a path the tools
        are about to refuse. Refusing is the call's job, not the preview's.
        """
        try:
            return inside(raw)
        except Outside:
            return None

    def holder(path: Path) -> Path | None:
        """Which root contains this path, if any."""
        for candidate in allowed:
            if path == candidate or candidate in path.parents:
                return candidate
        return None

    def inside(raw: str) -> Path:
        """Resolve a path the model wrote, or refuse it.

        Three ways in, tried in this order:

        1. absolute, or starting with "~" - expanded, then checked against every
           root. This is how a model refers back to a path it saw in a listing.
        2. led by a root's name, and only when there is more than one root. With
           Downloads and Documents both in play, "Documents/invoices" is
           unambiguous where "invoices" is not.
        3. relative to the first root.

        Rule 2 is skipped for a single root deliberately. There the first segment
        has never named a root, and a path that meant one thing yesterday must
        not quietly mean another today.
        """
        if raw.startswith("~") or raw.startswith("/"):
            target = Path(raw).expanduser().resolve()
        else:
            first, _, rest = raw.partition("/")
            if len(allowed) > 1 and first in named:
                target = (named[first] / rest).resolve()
            else:
                target = (root / raw).resolve()

        if holder(target) is None:
            where = ", ".join(str(r) for r in allowed)
            raise Outside(f"{raw!r} is outside {where}")
        return target

    def show(path: Path) -> str:
        """The path as the person will read it in a preview.

        Named when there is more than one root, bare when there is one - so a
        single-root preview reads exactly as it always has.
        """
        owner = holder(path)
        if owner is None:
            return str(path)
        if len(allowed) == 1:
            return str(path.relative_to(owner)) if path != owner else "."

        name = next(n for n, r in named.items() if r == owner)
        return name if path == owner else f"{name}/{path.relative_to(owner)}"

    def make_parents(path: Path) -> list[Path]:
        """Create the missing parents of `path`, returning them deepest first.

        Undo has to put the tree back as it was, and a folder aven invented on
        the way in is part of that. They come back deepest first so removing
        them in order is safe.
        """
        stop = holder(path) or root
        invented: list[Path] = []
        cursor = path.parent
        while cursor != stop and not cursor.exists():
            invented.append(cursor)
            cursor = cursor.parent

        path.parent.mkdir(parents=True, exist_ok=True)
        return invented

    def drop_invented(folders: list[Path]) -> None:
        """Remove folders we created, while they are still empty.

        Stops at the first one that is not: something else put a file there,
        and it is not aven's to delete.
        """
        for folder in folders:
            try:
                folder.rmdir()
            except OSError:
                return

    @tool(risk="read")
    def list_dir(
        path: Annotated[str, "Folder to list. '.' is the working folder"] = ".",
    ) -> str:
        """List the files and folders in one directory."""
        target = inside(path)
        if not target.is_dir():
            return f"not a directory: {path}"
        rows = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not rows:
            return "(empty)"
        return "\n".join(
            f"{'DIR ' if p.is_dir() else f'{p.stat().st_size:>7}'}  {p.name}" for p in rows
        )

    @tool(risk="read")
    def read_file(
        path: Annotated[str, "File to read"],
    ) -> str:
        """Read a text file."""
        target = inside(path)
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ:
            return text[:MAX_READ] + f"\n... [truncated, {len(text)} chars total]"
        return text

    @tool(risk="reversible", preview="write {path}",
          detail=lambda path, content, **_: Diff(
              path=path, before=_current(inside_quietly(path)), after=content))
    def write_file(
        path: Annotated[str, "File to write"],
        content: Annotated[str, "The full new contents of the file"],
    ) -> ToolResult:
        """Write a text file, creating or replacing it."""
        target = inside(path)
        existed = target.exists()
        before = target.read_text(encoding="utf-8") if existed else None

        invented = make_parents(target)
        target.write_text(content, encoding="utf-8")

        def undo() -> None:
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(before, encoding="utf-8")
            drop_invented(invented)

        verb = "replaced" if existed else "created"
        return ToolResult(output=f"{verb} {show(target)} ({len(content)} chars)", undo=undo)

    @tool(risk="reversible",
          preview=lambda path, old, **_: t("files.edit", path=path, old=_clip(old)),
          detail=lambda path, old, new, **_: Diff(path=path, before=old, after=new))
    def edit_file(
        path: Annotated[str, "File to change"],
        old: Annotated[str, "The exact text to replace, copied from the file"],
        new: Annotated[str, "What to put in its place"],
    ) -> ToolResult:
        """Replace one exact passage in a text file.

        Prefer this over write_file for anything but a new or tiny file: it
        costs the few lines that change rather than the whole document.
        """
        target = inside(path)
        before = target.read_text(encoding="utf-8")

        found = before.count(old)
        if found == 0:
            # The model is working from what read_file gave it, so a miss is
            # almost always whitespace. Say that rather than just "not found".
            raise NotFound(
                f"that text is not in {show(target)}. Copy it exactly as read_file "
                f"returned it, including indentation and line breaks."
            )
        if found > 1:
            raise Ambiguous(
                f"that text appears {found} times in {show(target)}. Include "
                f"enough surrounding lines to make it unique."
            )

        target.write_text(before.replace(old, new, 1), encoding="utf-8")
        return ToolResult(
            output=f"changed {show(target)} ({len(old)} chars → {len(new)})",
            undo=lambda: target.write_text(before, encoding="utf-8"),
        )

    @tool(risk="reversible", preview="{src} → {dst}")
    def move_file(
        src: Annotated[str, "Current path"],
        dst: Annotated[str, "New path"],
    ) -> ToolResult:
        """Move or rename a file."""
        source, target = inside(src), inside(dst)
        if target.is_dir():
            target = target / source.name
        if target.exists():
            raise FileExistsError(f"{show(target)} already exists")

        invented = make_parents(target)
        shutil.move(source, target)

        def undo() -> None:
            shutil.move(target, source)
            drop_invented(invented)

        return ToolResult(output=f"moved {show(source)} → {show(target)}", undo=undo)

    @tool(risk="reversible", preview=lambda path, **_: t("files.delete", path=path))
    def delete_file(
        path: Annotated[str, "File or folder to delete"],
    ) -> ToolResult:
        """Move a file to aven's trash. Recoverable until you empty it."""
        source = inside(path)
        bin_dir = trash / time.strftime("%Y-%m-%d")
        bin_dir.mkdir(parents=True, exist_ok=True)

        target = bin_dir / f"{int(time.time() * 1000)}-{source.name}"
        shutil.move(source, target)
        return ToolResult(
            output=f"moved {show(source)} to the trash",
            undo=lambda: shutil.move(target, source),
        )

    return [list_dir, read_file, write_file, edit_file, move_file, delete_file]
