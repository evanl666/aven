"""Session storage: one append-only JSONL file holding a tree of messages.

The file is a flat list of lines. The tree lives in the `parent_id` on each
message, so branching costs one more line - never a rewrite, never a new file.
That append-only discipline is what makes Step 5's undo possible: to go back you
move a pointer, you do not erase anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from aven.core.messages import Message, from_dict, to_dict


class Session:
    """A message tree backed by a JSONL file.

    `head` is the message the next append will hang off. Moving it is the whole
    of branching: check out an older node, append, and that node now has two
    children.
    """

    def __init__(self, path: Path, messages: dict[str, Message], head: str | None):
        self.path = path
        self.head = head
        self._messages = messages

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> Session:
        """Load a session file, or start one. Missing files are not an error."""
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        messages: dict[str, Message] = {}
        head: str | None = None

        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    msg = from_dict(json.loads(line))
                    messages[msg.id] = msg
                    # Last line wins: it was the head when the session was closed.
                    head = msg.id

        return cls(path, messages, head)

    def append(self, msg: Message) -> Message:
        """Hang a message off the current head and persist it.

        The caller never sets `parent_id` - the session owns the tree shape.
        The line is written before `head` moves, so a crash mid-write can leave
        a truncated line but never a message that exists in memory and not on
        disk.
        """
        msg.parent_id = self.head

        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(to_dict(msg), ensure_ascii=False) + "\n")

        self._messages[msg.id] = msg
        self.head = msg.id
        return msg

    # -- reading the tree ---------------------------------------------------

    def history(self, from_id: str | None = None) -> list[Message]:
        """The path from the root down to `from_id` (default: the head).

        This is what `to_llm` gets fed. Messages on other branches are in the
        file and in `_messages`, but they are not on this path, so the model
        never sees them.
        """
        node = self.head if from_id is None else from_id

        out: list[Message] = []
        seen: set[str] = set()
        while node is not None:
            if node in seen:
                raise ValueError(f"cycle in session tree at {node!r}")
            seen.add(node)

            msg = self._messages.get(node)
            if msg is None:
                raise ValueError(f"message {node!r} references a missing parent")
            out.append(msg)
            node = msg.parent_id

        out.reverse()
        return out

    def checkout(self, msg_id: str) -> None:
        """Point head at an existing message. The next append branches from it."""
        if msg_id not in self._messages:
            raise KeyError(f"no such message: {msg_id!r}")
        self.head = msg_id

    def children(self, msg_id: str | None) -> list[Message]:
        """Direct children, oldest first. `None` gives the roots."""
        return sorted(
            (m for m in self._messages.values() if m.parent_id == msg_id),
            key=lambda m: m.ts,
        )

    def leaves(self) -> list[Message]:
        """Messages with no children - one per branch tip."""
        parents = {m.parent_id for m in self._messages.values()}
        return sorted(
            (m for m in self._messages.values() if m.id not in parents),
            key=lambda m: m.ts,
        )

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"<Session {self.path.name} {len(self)} messages head={self.head!r}>"
