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
        if msg.id in self._messages:
            # append mutates parent_id, so re-appending the same object would
            # quietly make it its own ancestor. Fail here, not three calls later
            # inside history().
            raise ValueError(f"message {msg.id!r} is already in this session")

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

    def find(self, prefix: str) -> str:
        """Resolve a shortened id to a full one.

        Ids get read off a screen and typed back, so a prefix has to be enough.
        An ambiguous one is an error rather than a guess: picking the first
        match would silently check out the wrong branch, and the whole point of
        the tree is that the other branch is still there.
        """
        if prefix in self._messages:
            return prefix

        matches = [i for i in self._messages if i.startswith(prefix)]
        if not matches:
            raise KeyError(f"no message starting with {prefix!r}")
        if len(matches) > 1:
            raise KeyError(
                f"{prefix!r} matches {len(matches)}: {', '.join(i[:8] for i in matches[:4])}"
            )
        return matches[0]

    def fork(self, path: str | Path, *, at: str | None = None) -> Session:
        """Copy the path down to `at` into a new session file.

        The alternative to branching in place. Checking out an older message
        keeps both attempts in one file, which is right when they are versions
        of the same thing; a fork is for when the detour has become its own
        piece of work and should stop sharing a file - and a history - with
        what it came from.

        Ids are carried over rather than reissued. The copy is a prefix of this
        session, so the parent links are already consistent, and keeping them
        means an id quoted in a note still finds the same message in either
        file. Nothing in this session changes; the fork only ever reads.
        """
        messages = self.history(self.find(at) if at else None)

        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"{path} already exists")

        with path.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(to_dict(msg), ensure_ascii=False) + "\n")

        return Session.open(path)

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
