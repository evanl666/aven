"""Reading the session tree, for a person rather than for the model.

The tree has been in the file since the first step: every message carries a
`parent_id`, checkout moves the head, and appending after an older message gives
it a second child. Nothing has ever shown it. That is the whole of this module -
no new storage, no new state, just a view over what `Session` already holds.

Two decisions about what to show:

Tool results are left out. A branch point is a moment someone might want to
return to, and nobody returns to the middle of a directory listing - they return
to what they asked, or to the answer they got. Fifty lines of tool output
between two of those hides the one thing the view is for.

Ids are shortened to six characters. They are picked by hand off a screen, so
they have to be short enough to retype, and `find` accepts any unambiguous
prefix so that being short costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from aven.harness.messages import (
    AssistantMessage,
    Message,
    SummaryMessage,
    UserMessage,
)
from aven.harness.session import Session
from aven.text import t

SHORT = 6

# How much of a message to put on its line. Long enough to recognise which of
# two attempts this was, short enough that the shape of the tree stays visible.
BLURB = 54


def interesting(message: Message) -> bool:
    """Whether a message is a place someone might want to go back to."""
    if isinstance(message, UserMessage):
        return True
    if isinstance(message, SummaryMessage):
        return True
    return isinstance(message, AssistantMessage) and bool(message.text)


@dataclass(frozen=True)
class Node:
    message: Message
    depth: int
    on_path: bool
    is_head: bool


def walk(session: Session) -> list[Node]:
    """Every interesting message, depth first, oldest sibling first.

    `depth` counts branch points passed through, not messages, so a
    conversation that never branched comes back perfectly flat - which is what
    it is, and the common case.
    """
    on_path = {m.id for m in session.history()} if session.head else set()

    nodes: list[Node] = []

    def descend(parent: str | None, depth: int) -> None:
        children = session.children(parent)
        for child in children:
            # Only a real fork earns indentation.
            deeper = depth + 1 if len(children) > 1 else depth
            if interesting(child):
                nodes.append(
                    Node(
                        message=child,
                        depth=deeper,
                        on_path=child.id in on_path,
                        is_head=child.id == session.head,
                    )
                )
            descend(child.id, deeper)

    descend(None, 0)
    return nodes


def render(session: Session) -> str:
    """The tree as lines to print, head marked."""
    nodes = walk(session)
    if not nodes:
        return t("tree.empty")

    lines = [_line(node) for node in nodes]
    branches = len(session.leaves())
    tail = t("tree.footer", nodes=len(nodes), branches=branches)
    return "\n".join(lines) + "\n\n" + tail


def _line(node: Node) -> str:
    message = node.message
    mark = "▸" if node.is_head else ("│" if node.on_path else " ")
    who = t(f"tree.who.{message.kind}") if message.kind in (
        "user", "assistant", "summary"
    ) else message.kind

    text = getattr(message, "text", "").replace("\n", " ").strip()
    if len(text) > BLURB:
        text = text[: BLURB - 1] + "…"

    indent = "  " * node.depth
    return f"{mark} {message.id[:SHORT]}  {indent}{_pad(who, 5)}{text}"


def _pad(text: str, width: int) -> str:
    """Pad to a column width in terminal cells, not in characters.

    A CJK character is one character and two cells wide, so str.ljust lines the
    column up in the source and not on the screen - which is the only place it
    matters.
    """
    cells = sum(2 if ord(c) > 0x2E80 else 1 for c in text)
    return text + " " * max(1, width - cells)
