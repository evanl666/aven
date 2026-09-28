"""MCP servers, as connectors.

An MCP server is a process that exposes a set of tools over JSON-RPC on stdin
and stdout. That is the same shape as a tool group, so it maps onto aven's
machinery with nothing bent: one server is one connector, its tools are that
connector's tools, and bringing the group in is what starts the process.

This is the answer to "any service the user wants to connect". Writing a
connector by hand costs an OAuth flow and an API wrapper per service; pointing
at an MCP server costs four lines of config, and somebody else maintains it.

## The part that is a security decision, not plumbing

**A server's own account of how dangerous its tools are cannot be trusted.**

MCP lets a tool carry `readOnlyHint`. It is a hint from the very party whose
behaviour is in question - a server that deletes your files can describe itself
as read-only, and a server that is merely careless can be wrong by accident. A
harness whose entire claim is "nothing irreversible happens without you" cannot
take that at face value.

So: **every MCP tool is `irreversible` until somebody says otherwise**, which
means it is staged and waits for a decision. That is deliberately inconvenient.
The two ways out are both the person's own words, never the server's:

    trust = true          honour the server's readOnlyHint for read-only tools
    [mcp.<name>.risk]     name a tool and give it a risk yourself

Guessing from the name was considered and rejected. `list_files` sounds safe;
`list_files` in a server nobody audited is a function the author chose the name
of. A convention that can be spelled to defeat it is not a control.

**Names are prefixed with the server's.** Two servers both offering `read_file`
would otherwise collide, and worse, an MCP `read_file` could shadow aven's own -
the one with the root sandbox around it. `files_read_file` can never be mistaken
for the built-in.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

from aven.harness.tools import Risk, Tool, ToolResult

# A server that has not answered in this long is not going to. Long enough for a
# cold `npx` to fetch a package on a slow connection.
PATIENCE = 60
START = 90


class Unreachable(Exception):
    """The server is not running, or stopped answering."""


@dataclass
class Spec:
    """How to start one server, and how much to believe it."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    about: str = ""

    # Whether `readOnlyHint` is honoured. Off by default: see the module docstring.
    trust: bool = False

    # The person's own risk per tool name, before prefixing. Beats everything.
    risk: dict[str, Risk] = field(default_factory=dict)


class Server:
    """One MCP server process, started when it is first needed.

    Lazy on purpose. A person with six servers configured should not be paying
    for six processes to start, and six packages to be fetched, because they
    opened a window to ask about a file.
    """

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.process: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.counter = 0
        self.listed: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    # -- the process ---------------------------------------------------------

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        import os

        try:
            self.process = subprocess.Popen(
                [self.spec.command, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # commentary, never protocol
                text=True,
                bufsize=1,  # line buffered: a whole record or nothing
                cwd=self.spec.cwd,
                env={**os.environ, **self.spec.env} if self.spec.env else None,
            )
        except (OSError, ValueError) as problem:
            raise Unreachable(f"could not start {self.spec.name}: {problem}") from problem

        # Read on a thread. A blocking readline on the main one would hang the
        # whole agent on a server that stops talking, and the point of a timeout
        # is that something else is still running to notice it.
        threading.Thread(target=self._drain, daemon=True).start()

        self._ask("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aven", "version": "0"},
        }, patience=START)
        self._tell("notifications/initialized", {})
        self.listed = self._ask("tools/list", {}).get("tools", [])

    def _drain(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put("")  # sentinel: the pipe closed

    def stop(self) -> None:
        if self.process is None:
            return
        # Closing stdin is how an MCP server is asked to leave. Killing it would
        # be the same abrupt end this project avoids everywhere else.
        try:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        finally:
            self.process = None

    # -- JSON-RPC ------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise Unreachable(f"{self.spec.name} is not running")
        try:
            self.process.stdin.write(json.dumps(record) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as gone:
            raise Unreachable(f"{self.spec.name} stopped listening") from gone

    def _tell(self, method: str, params: dict[str, Any]) -> None:
        """A notification: no id, so no answer is coming."""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _ask(
        self, method: str, params: dict[str, Any], patience: float = PATIENCE
    ) -> dict[str, Any]:
        """A request, and the answer to it.

        Held under a lock for the whole round trip. Two tool calls landing at
        once would otherwise interleave on one pipe, and each could be handed
        the other's answer - the intermittent kind of wrong that is worst to
        find later.
        """
        with self.lock:
            self.counter += 1
            mine = self.counter
            self._write({"jsonrpc": "2.0", "id": mine, "method": method, "params": params})

            while True:
                try:
                    line = self.lines.get(timeout=patience)
                except queue.Empty:
                    raise Unreachable(
                        f"{self.spec.name} did not answer {method} in {patience:.0f}s"
                    ) from None
                if line == "":
                    raise Unreachable(f"{self.spec.name} closed the connection")

                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # noise on stdout is the server's problem, not ours

                # Anything without our id is a notification or somebody else's
                # answer; neither is what this call is waiting for.
                if record.get("id") != mine:
                    continue
                if "error" in record:
                    problem = record["error"]
                    raise Unreachable(
                        f"{self.spec.name}: {problem.get('message', problem)}"
                    )
                return record.get("result", {})

    # -- what aven wants -----------------------------------------------------

    def tools(self) -> list[Tool]:
        """The server's tools, as aven tools. Starts it if it is not up."""
        self.start()
        return [self._as_tool(declared) for declared in self.listed]

    def _as_tool(self, declared: dict[str, Any]) -> Tool:
        theirs = str(declared.get("name", ""))
        mine = f"{self.spec.name}_{theirs}"
        schema = declared.get("inputSchema") or {"type": "object", "properties": {}}

        def run(**args: Any) -> ToolResult:
            answer = self._ask("tools/call", {"name": theirs, "arguments": args})
            text = _readable(answer)
            if answer.get("isError"):
                raise RuntimeError(text)
            return ToolResult(output=text)

        return Tool(
            name=mine,
            description=str(declared.get("description", "")).strip(),
            schema=schema,
            risk=self._risk_of(declared),
            fn=run,
            preview_with=lambda **args: (
                f"{self.spec.name}: {theirs}("
                + ", ".join(f"{k}={v!r}" for k, v in args.items())
                + ")"
            ),
            # Never. A standing approval is a decision made in advance about
            # something whose behaviour is known, and an outside server's tool
            # is the case where it is least known. Nothing here is covered by
            # one, ever.
            pre_approvable=False,
        )

    def _risk_of(self, declared: dict[str, Any]) -> Risk:
        """How much this call is allowed to be trusted, and on whose word.

        In order: what the person wrote, then the server's hint but only if the
        person marked it trusted, then the safe answer.
        """
        theirs = str(declared.get("name", ""))
        said = self.spec.risk.get(theirs)
        if said in ("read", "reversible", "irreversible"):
            return said

        if self.spec.trust:
            hints = declared.get("annotations") or {}
            if hints.get("readOnlyHint") is True:
                return "read"
            if hints.get("destructiveHint") is False:
                return "reversible"

        return "irreversible"


def _readable(answer: dict[str, Any]) -> str:
    """An MCP result, flattened to the string a model can read.

    Content is a list of parts, each of a declared type. Only text is turned
    into text; anything else is named rather than dumped, because a base64 image
    inlined into a transcript is tens of thousands of tokens nobody can read.
    """
    parts = answer.get("content")
    if not isinstance(parts, list):
        return json.dumps(answer)[:4000]

    said: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            said.append(str(part.get("text", "")))
        else:
            said.append(f"[{part.get('type', 'something')} the model cannot read here]")
    return "\n".join(said) or "(nothing)"
