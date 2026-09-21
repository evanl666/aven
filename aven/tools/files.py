"""File tools, scoped to one root.

Every path the model supplies is resolved and checked against the root before
anything happens. A model that has read a hostile web page will eventually ask
for ../../.ssh/id_rsa; the answer has to be no at the tool boundary, not in the
prompt, because the prompt is exactly what the attacker got to write.

Deletion moves to an aven-owned trash directory rather than unlinking, which is
what makes it reversible - and reversible work needs no approval.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Annotated

from aven.core.tools import Tool, ToolResult, tool

MAX_READ = 40_000  # characters; enough for source and notes, not for a video


class Outside(Exception):
    """A path resolved to somewhere outside the root."""


def file_tools(root: Path) -> list[Tool]:
    """Build the file toolset bound to one directory."""
    root = Path(root).expanduser().resolve()
    trash = Path.home() / ".aven" / "trash"

    def inside(raw: str) -> Path:
        # "~/..." means the home directory to whoever wrote it. Joining it onto
        # the root instead would quietly look for a folder literally named "~"
        # and fail with a confusing FileNotFoundError, so say no plainly.
        if raw.startswith("~"):
            raise Outside(f"{raw!r} is outside {root}")

        # resolve() collapses "..", symlinks and all, so the comparison below is
        # against the real destination rather than the string the model wrote.
        target = (root / raw).resolve()
        if target != root and root not in target.parents:
            raise Outside(f"{raw!r} is outside {root}")
        return target

    def show(path: Path) -> str:
        return str(path.relative_to(root)) if path != root else "."

    def make_parents(path: Path) -> list[Path]:
        """Create the missing parents of `path`, returning them deepest first.

        Undo has to put the tree back as it was, and a folder aven invented on
        the way in is part of that. They come back deepest first so removing
        them in order is safe.
        """
        invented: list[Path] = []
        cursor = path.parent
        while cursor != root and not cursor.exists():
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
        path: Annotated[str, "Folder to list, relative to the root. Use '.' for the root"] = ".",
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
        path: Annotated[str, "File to read, relative to the root"],
    ) -> str:
        """Read a text file."""
        target = inside(path)
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ:
            return text[:MAX_READ] + f"\n... [truncated, {len(text)} chars total]"
        return text

    @tool(risk="reversible", preview="write {path}")
    def write_file(
        path: Annotated[str, "File to write, relative to the root"],
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

    @tool(risk="reversible", preview="{src} → {dst}")
    def move_file(
        src: Annotated[str, "Current path, relative to the root"],
        dst: Annotated[str, "New path, relative to the root"],
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

    @tool(risk="reversible", preview="删除 {path}")
    def delete_file(
        path: Annotated[str, "File or folder to delete, relative to the root"],
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

    return [list_dir, read_file, write_file, move_file, delete_file]
