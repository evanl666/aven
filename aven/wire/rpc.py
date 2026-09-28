"""aven driven by another program.

A client sends commands and receives responses and events. Which transport
carries them is not this module's business: `Conversation` takes an `emit`
callback and never touches a pipe or a socket, so the same dispatcher serves the
stdio loop below and the WebSocket a phone will use later.

Three things about the design are worth knowing before reading it.

**The tray lives as long as the connection, not as long as a prompt.** A tray
made per prompt would drop whatever the previous turn staged, and the whole point
of a separate approvals surface is that you can come back to it. It is replaced
only once nothing in it is waiting or undoable, exactly as the full-screen app
does it.

**A prompt while busy is queued, not refused.** That is what the terminal app
does, it is what a chat window wants, and the machinery is the steering queue
that already existed. The response says which happened, so a client can draw the
difference.

**`agent_end` does not mean aven has stopped.** A follow-up in the steering queue
carries the run on, so a client waiting to re-enable its input box needs a
stronger signal: `settled` is emitted when the run task is really finished.

One consequence worth knowing before writing a client: **a response is not
ordered against the events.** A prompt's run is started before its response is
written - so that "started" is true when we say it, and an interrupt arriving
next cancels something that actually began - which means `agent_start` can reach
a client ahead of the `{"command": "prompt"}` response. Correlate by `id`, never
by position.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from aven.harness.agent import run
from aven.harness.messages import Message
from aven.harness.session import Session
from aven.harness.sessions import catalogue
from aven.harness.steering import Steering
from aven.harness.toolbox import ToolBox, resolve
from aven.harness.tree import walk
from aven.harness.tx import Policy, Standing, Tray
from aven.wire import protocol
from aven.wire.protocol import failed, ok

Emit = Callable[[dict[str, Any]], None]


class Conversation:
    """One client driving one session."""

    def __init__(
        self,
        *,
        session: Session,
        model: Any,
        box: ToolBox,
        describe: dict[str, str] | None = None,
        policy: Policy | None = None,
        standing: Standing | None = None,
        compactor: Any = None,
        sessions_dir: Any = None,
        max_turns: int = 12,
        emit: Emit,
    ) -> None:
        self.session = session
        self.model = model
        self.box = box
        self.describe = describe or {}
        self.policy = policy
        self.standing = standing
        self.compactor = compactor
        self.sessions_dir = sessions_dir
        self.max_turns = max_turns
        self.emit = emit

        self.tray = Tray(policy=policy, standing=standing)
        self.steering = Steering()
        self.task: asyncio.Task[None] | None = None
        self.stopped = False

    # -- state ---------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def state(self) -> dict[str, Any]:
        """Everything a client that just connected needs to draw itself."""
        return {
            "version": protocol.VERSION,
            "session": {
                "path": str(self.session.path),
                "name": self.session.name,
                "messages": len(self.session),
                "branches": len(self.session.leaves()),
                "head": self.session.head,
            },
            "busy": self.busy,
            "queued": self.steering.waiting(),
            "tools": [tool.name for tool in resolve(self.box.active)],
            "connectors": protocol.connectors_as_dict(self.box, self.describe),
            "tray": protocol.tray_as_dict(self.tray),
            "standing": [str(a) for a in (self.standing.approvals if self.standing else [])],
            "usage": protocol.usage_as_dict(getattr(self.model, "usage", None)),
        }

    # -- the dispatcher ------------------------------------------------------

    async def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        """One command in, one response out. Never raises.

        A command that fails comes back as a response saying so. A dispatcher
        that can raise would take the connection down over a typo, and the
        client would have no way to tell that from aven crashing.
        """
        kind = str(command.get("type", ""))
        id = command.get("id")

        handler = getattr(self, f"_do_{kind}", None)
        if handler is None:
            return failed(kind or "?", f"no such command: {kind!r}", id=id)

        try:
            data = await handler(command)
        except Exception as problem:
            return failed(kind, f"{type(problem).__name__}: {problem}", id=id)
        return ok(kind, data, id=id)

    # -- talking -------------------------------------------------------------

    async def _do_prompt(self, command: dict[str, Any]) -> dict[str, Any]:
        message = str(command.get("message", "")).strip()
        if not message:
            raise ValueError("a prompt needs a message")

        if self.busy:
            # Queued rather than refused: the same thing the full-screen app
            # does, and what a chat window wants.
            self.steering.add(message)
            return {"disposition": "queued", "queued": self.steering.waiting()}

        source = str(command.get("source", "chat"))
        self.task = asyncio.create_task(self._run(message, source))

        # `settled` comes from the task finishing, not from inside it. A task
        # cancelled before its body ever ran never reaches a `finally`, and a
        # client that sent prompt-then-interrupt would be left believing aven is
        # still busy with nothing coming to say otherwise.
        self.task.add_done_callback(self._finished)

        # One turn of the loop, so "started" is true when we say it. Without
        # this the task is only scheduled, and an interrupt arriving next would
        # cancel a coroutine that never began - no agent_start, no events, no
        # explanation.
        await asyncio.sleep(0)
        return {"disposition": "started"}

    async def _do_steer(self, command: dict[str, Any]) -> dict[str, Any]:
        """Add to the queue whether or not a run is in flight.

        Separate from `prompt` so a client can be explicit. Steering a stopped
        agent is not an error - it becomes the next thing said.
        """
        message = str(command.get("message", "")).strip()
        if not message:
            raise ValueError("steering needs a message")
        self.steering.add(message)
        return {"queued": self.steering.waiting()}

    async def _do_interrupt(self, command: dict[str, Any]) -> dict[str, Any]:
        if not self.busy or self.task is None:
            return {"interrupted": False}
        self.task.cancel()
        # Whatever was queued was never read, so it goes back to the client
        # rather than being thrown away with the run that would have read it.
        return {"interrupted": True, "returned": self.steering.drain()}

    async def _run(self, prompt: str, source: str) -> None:
        """Drive one run, emitting as it goes."""
        try:
            async for event in run(
                session=self.session,
                prompt=prompt,
                model=self.model,
                tools=self.box.active,
                tray=self.tray,
                compactor=self.compactor,
                steering=self.steering,
                max_turns=self.max_turns,
                source=source,
            ):
                self.emit(protocol.event_as_dict(event))
                if event.__class__.__name__ == "ToolEnd":
                    # The approvals pane should not have to poll to notice that
                    # something is now waiting on it.
                    self.emit({"type": "tray", **protocol.tray_as_dict(self.tray)})
        except asyncio.CancelledError:
            self.emit({"type": "interrupted"})
            raise
        except Exception as problem:
            self.emit({
                "type": "failed",
                "error": f"{type(problem).__name__}: {problem}",
            })
        finally:
            self.emit({"type": "tray", **protocol.tray_as_dict(self.tray)})

    def _finished(self, task: asyncio.Task[None]) -> None:
        """Say the run is over, however it ended.

        agent_end is not this: a follow-up in the steering queue carries the run
        past it, so a client re-enabling its input box on agent_end would do it
        mid-run. This fires for a clean finish, a failure, and a cancellation -
        including one that arrived before the body had started.
        """
        self.emit({"type": "settled", "busy": False})

    # -- deciding ------------------------------------------------------------

    async def _do_pending(self, command: dict[str, Any]) -> dict[str, Any]:
        return protocol.tray_as_dict(self.tray)

    async def _do_approve(self, command: dict[str, Any]) -> dict[str, Any]:
        """Fire the entries named, and only those.

        `ids` absent means all of them, because "approve everything" is a thing
        a client offers; `ids: []` means none, which is not the same and must not
        be read as the first.
        """
        ids = command.get("ids")
        if ids is not None and not isinstance(ids, list):
            raise ValueError("ids has to be a list")

        done = await asyncio.to_thread(
            self.tray.commit, None if ids is None else [str(i) for i in ids]
        )
        failures = [protocol.entry_as_dict(e) for e in self.tray.entries
                    if e.state == "failed"]
        self._settle()
        return {
            "committed": [protocol.entry_as_dict(e) for e in done],
            "failed": failures,
            "tray": protocol.tray_as_dict(self.tray),
        }

    async def _do_discard(self, command: dict[str, Any]) -> dict[str, Any]:
        dropped = self.tray.discard()
        self._settle()
        return {"discarded": len(dropped), "tray": protocol.tray_as_dict(self.tray)}

    async def _do_undo(self, command: dict[str, Any]) -> dict[str, Any]:
        point = self.tray.rewind_point()
        rolled = await asyncio.to_thread(self.tray.undo)
        # The world went back; the conversation has to go back with it, or the
        # model carries on believing the work still stands.
        if rolled and point:
            self.session.checkout(point)
        self._settle()
        return {
            "undone": len(rolled),
            "head": self.session.head,
            "tray": protocol.tray_as_dict(self.tray),
        }

    def _settle(self) -> None:
        """Start a fresh tray once nothing in this one waits on a decision."""
        if not self.tray.pending() and not self.tray.undoable():
            self.tray = Tray(policy=self.policy, standing=self.standing)

    # -- connecting ----------------------------------------------------------

    async def _do_connect(self, command: dict[str, Any]) -> dict[str, Any]:
        """Bring a tool group in - which is what "connect a service" means here."""
        group = str(command.get("group", ""))
        brought = self.box.bring_in(group)
        if not brought and group not in self.box.groups:
            raise KeyError(f"no connector called {group!r}")
        return {
            "connected": group,
            "already": not brought,
            "connectors": protocol.connectors_as_dict(self.box, self.describe),
        }

    # -- reading -------------------------------------------------------------

    async def _do_state(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.state()

    async def _do_transcript(self, command: dict[str, Any]) -> dict[str, Any]:
        return {"messages": protocol.transcript_as_dict(self.session.history())}

    async def _do_tree(self, command: dict[str, Any]) -> dict[str, Any]:
        return {"nodes": [protocol.node_as_dict(n) for n in walk(self.session)]}

    async def _do_sessions(self, command: dict[str, Any]) -> dict[str, Any]:
        if self.sessions_dir is None:
            return {"sessions": []}
        return {
            "sessions": [
                protocol.card_as_dict(card) for card in catalogue(self.sessions_dir)
            ]
        }

    async def _do_name(self, command: dict[str, Any]) -> dict[str, Any]:
        name = str(command.get("name", "")).strip()
        if not name:
            return {"name": self.session.name}
        self.session.rename(name)
        return {"name": self.session.name}

    async def _do_checkout(self, command: dict[str, Any]) -> dict[str, Any]:
        """Move to another point in the tree.

        The tray belongs to the branch being left: its undos point at work done
        on a path we are no longer on, so it is replaced rather than carried.
        """
        if self.busy:
            raise RuntimeError("interrupt the run first")
        target = self.session.find(str(command.get("id", "")))
        self.session.checkout(target)
        self.tray = Tray(policy=self.policy, standing=self.standing)
        return {"head": self.session.head, "tray": protocol.tray_as_dict(self.tray)}

    async def _do_shutdown(self, command: dict[str, Any]) -> dict[str, Any]:
        if self.busy and self.task is not None:
            self.task.cancel()
        self.stopped = True
        return {"stopped": True}


# --- the stdio transport -----------------------------------------------------
#
# Strict JSONL: one complete object per line, terminated by LF. Read stdout as a
# byte or UTF-8 stream and split only on LF - a generic line reader may also
# split on U+2028 and U+2029, which are legal inside a JSON string. Node's
# `readline` does exactly that.


async def serve_stdio(conversation: Conversation) -> int:
    """Read commands from stdin, write responses and events to stdout.

    stdout carries protocol records and nothing else; diagnostics go to stderr.
    Every record is flushed as it is written, because a client is reading this as
    a stream and a block-buffered pipe would hold a turn's events until the next
    one filled it.
    """
    loop = asyncio.get_running_loop()

    while not conversation.stopped:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break  # the client closed stdin: an orderly shutdown
        line = line.strip()
        if not line:
            continue

        try:
            command = json.loads(line)
        except ValueError as broken:
            write({"type": "response", "command": "parse", "ok": False,
                   "error": f"could not parse that line: {broken}"})
            continue
        if not isinstance(command, dict):
            write({"type": "response", "command": "parse", "ok": False,
                   "error": "a command has to be an object"})
            continue

        write(await conversation.handle(command))

    if conversation.busy and conversation.task is not None:
        conversation.task.cancel()
    return 0


def write(record: dict[str, Any]) -> None:
    json.dump(record, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()
