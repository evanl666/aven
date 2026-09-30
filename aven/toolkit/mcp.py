"""MCP servers, as connectors.

An MCP server exposes a set of tools over JSON-RPC. That is the same shape as a
tool group, so it maps onto aven's machinery with nothing bent: one server is
one connector, its tools are that connector's tools, and bringing the group in
is what opens the connection.

This is the answer to "any service the user wants to connect". Writing a
connector by hand costs an OAuth flow and an API wrapper per service; pointing
at an MCP server costs four lines of config, and somebody else maintains it.

## Two ways to reach one

    command = "npx"                     a process on this machine, over its
    args = ["-y", "some-server"]        stdin and stdout

    url = "https://example.com/mcp"     a server somewhere else, over HTTP

Everything above the transport is the same for both, which is why `Talks` holds
it and the two transports hold only the bytes. What a tool is, how risky it is,
how it is named and how its result is read does not depend on how far away it
happens to be.

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

This matters more for a remote server, not less. A local one at least runs as
you, on your machine, under whatever the OS already stops it doing. A remote one
is somebody else's computer, and "read-only" is their word for it.

**Names are prefixed with the server's.** Two servers both offering `read_file`
would otherwise collide, and worse, an MCP `read_file` could shadow aven's own -
the one with the root sandbox around it. `files_read_file` can never be mistaken
for the built-in.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from aven.harness.tools import Body, Detail, Risk, Tool, ToolResult

# A server that has not answered in this long is not going to. Long enough for a
# cold `npx` to fetch a package on a slow connection.
PATIENCE = 60
START = 90

# Kept out of a server's environment unless its config asks for it by name.
#
# A local MCP server runs as you, with your files and your network. aven's
# approval tray governs what the *model* asks a server to do; it governs
# nothing about what the server's own process does, which begins the moment it
# starts. That is worth knowing and mostly cannot be fixed from here.
#
# What can be fixed is what is handed over for free. aven puts the API key into
# its own environment so the SDK can read it, and a child inheriting the whole
# environment was therefore given a working key by aven itself - to a server
# that has no use for one. A server that genuinely needs a credential gets it
# through `[mcp.<name>.env]`, where somebody wrote it down on purpose.
WITHHELD = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)


def _child_environment(asked: dict[str, str]) -> dict[str, str]:
    """The environment a server is started with.

    Inherited, minus the credentials above, plus whatever its own config names.
    Anything listed in the config wins: somebody writing `GITHUB_TOKEN` under
    `[mcp.x.env]` means that server should have it.
    """
    passed = {k: v for k, v in os.environ.items() if k not in WITHHELD}
    passed.update(asked)
    return passed

# The revision of MCP this speaks. Sent on initialize; a server that cannot do
# it says so rather than guessing.
SPEAKS = "2024-11-05"


class Unreachable(Exception):
    """The server is not running, or stopped answering."""


@dataclass
class Spec:
    """How to reach one server, and how much to believe it."""

    name: str
    about: str = ""

    # Over a pipe to a process here...
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    # ...or over HTTP to one somewhere else. Exactly one of these.
    url: str = ""
    # Anything with a `token()`, which is what `toolkit/oauth.py` provides. A
    # remote server that needs no credential simply has none.
    auth: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    # Whether `readOnlyHint` is honoured. Off by default: see the module docstring.
    trust: bool = False

    # The person's own risk per tool name, before prefixing. Beats everything.
    risk: dict[str, Risk] = field(default_factory=dict)

    @property
    def remote(self) -> bool:
        return bool(self.url)


class Talks:
    """One MCP server, once something can be said to it.

    Holds everything that does not depend on how it is reached: the handshake,
    what a tool is, how risky it is, and what its result says. A transport
    supplies `_open`, `_ask`, `_tell` and `stop`.
    """

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.listed: list[dict[str, Any]] = []
        self.counter = 0
        self.lock = threading.Lock()

    # -- what a transport provides -------------------------------------------

    def _open(self) -> bool:
        """Get the connection up. True if it is new and needs a handshake."""
        raise NotImplementedError

    def _ask(self, method: str, params: dict[str, Any], patience: float = PATIENCE) -> dict[str, Any]:
        raise NotImplementedError

    def _tell(self, method: str, params: dict[str, Any]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    # -- the handshake, which is the same either way -------------------------

    def start(self) -> None:
        if not self._open():
            return
        self._ask("initialize", {
            "protocolVersion": SPEAKS,
            "capabilities": {},
            "clientInfo": {"name": "aven", "version": "0"},
        }, patience=START)
        self._tell("notifications/initialized", {})
        self.listed = self._ask("tools/list", {}).get("tools", [])

    def _next_id(self) -> int:
        self.counter += 1
        return self.counter

    # -- what aven wants -----------------------------------------------------

    def tools(self) -> list[Tool]:
        """The server's tools, as aven tools. Connects if it is not connected."""
        self.start()
        return [self._as_tool(declared) for declared in self.listed]

    def _as_tool(self, declared: dict[str, Any]) -> Tool:
        theirs = str(declared.get("name", ""))
        mine = self._named(theirs)
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
            preview_with=lambda **args: _one_line(self.spec.name, theirs, args),
            detail_with=lambda **args: _in_full(theirs, args),
            # Never. A standing approval is a decision made in advance about
            # something whose behaviour is known, and an outside server's tool
            # is the case where it is least known. Nothing here is covered by
            # one, ever.
            pre_approvable=False,
        )

    def _named(self, theirs: str) -> str:
        """The server's name in front, unless it is already there.

        A server called `browser` whose tools are `browser_click` would become
        `browser_browser_click`, which is noise in every request for as long as
        the group is loaded. The prefix exists so two servers cannot collide and
        so nothing can shadow a built-in; a name that already carries it does
        both jobs already.
        """
        if theirs.startswith(f"{self.spec.name}_"):
            return theirs
        return f"{self.spec.name}_{theirs}"

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


class Server(Talks):
    """A server running as a process here, spoken to over its pipes.

    Lazy on purpose. A person with six servers configured should not be paying
    for six processes to start, and six packages to be fetched, because they
    opened a window to ask about a file.
    """

    def __init__(self, spec: Spec) -> None:
        super().__init__(spec)
        self.process: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()

    def _open(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return False

        try:
            self.process = subprocess.Popen(
                [self.spec.command, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # commentary, never protocol
                text=True,
                bufsize=1,  # line buffered: a whole record or nothing
                cwd=self.spec.cwd,
                env=_child_environment(self.spec.env),
            )
        except (OSError, ValueError) as problem:
            raise Unreachable(f"could not start {self.spec.name}: {problem}") from problem

        # Read on a thread. A blocking readline on the main one would hang the
        # whole agent on a server that stops talking, and the point of a timeout
        # is that something else is still running to notice it.
        threading.Thread(target=self._drain, daemon=True).start()
        return True

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
            self.listed = []

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
            mine = self._next_id()
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
                return _result(self.spec.name, record)


class Remote(Talks):
    """A server somewhere else, spoken to over Streamable HTTP.

    One POST per message. The answer comes back either as a JSON object or as an
    event stream, and the server chooses - so both are read, and the stream is
    read only until the reply we are waiting for arrives.

    A session id, when the server issues one, is the thread tying the requests
    together. It arrives as a header on the initialize response and has to be
    sent on everything after it; without it the second request looks like a
    stranger and is refused.
    """

    def __init__(self, spec: Spec) -> None:
        super().__init__(spec)
        self.session: str | None = None
        self.open = False

    def _open(self) -> bool:
        if self.open:
            return False
        self.session = None
        self.open = True
        return True

    def stop(self) -> None:
        """Let the server drop the session, and forget it here either way.

        Best effort. The session expires on its own, and failing to close one
        tidily is not a reason to fail the disconnect somebody asked for.
        """
        if self.session:
            try:
                self._send(None, method="DELETE")
            except Exception:
                pass
        self.session = None
        self.open = False
        self.listed = []

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Both, because the server picks. Saying we take only JSON would
            # refuse a perfectly good streamed reply.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": SPEAKS,
            **self.spec.headers,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.spec.auth is not None:
            # Fetched per request, so a token that expired mid-conversation is
            # refreshed rather than sent stale.
            headers["Authorization"] = f"Bearer {self.spec.auth.token()}"
        return headers

    def _send(
        self, record: dict[str, Any] | None, *, method: str = "POST", patience: float = PATIENCE
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            self.spec.url,
            method=method,
            data=json.dumps(record).encode() if record is not None else None,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=patience) as answer:
                return answer.status, dict(answer.headers), answer.read()
        except urllib.error.HTTPError as refused:
            detail = refused.read().decode(errors="replace")[:300]
            if refused.code in (401, 403):
                raise Unreachable(
                    f"{self.spec.name} refused this ({refused.code}). It may need "
                    f"connecting again: {detail}"
                ) from refused
            raise Unreachable(
                f"{self.spec.name} returned {refused.code}: {detail}"
            ) from refused
        except (urllib.error.URLError, TimeoutError) as unreachable:
            raise Unreachable(
                f"could not reach {self.spec.name}: {unreachable}"
            ) from unreachable

    def _tell(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _ask(
        self, method: str, params: dict[str, Any], patience: float = PATIENCE
    ) -> dict[str, Any]:
        with self.lock:
            mine = self._next_id()
            status, headers, body = self._send(
                {"jsonrpc": "2.0", "id": mine, "method": method, "params": params},
                patience=patience,
            )

            # Issued once, on initialize, and required on everything after.
            given = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
            if given:
                self.session = given

            for record in _records(headers.get("Content-Type", ""), body):
                if record.get("id") != mine:
                    continue  # a notification, or somebody else's answer
                return _result(self.spec.name, record)

            raise Unreachable(
                f"{self.spec.name} answered {method} with nothing that matched"
            )


def open_server(spec: Spec) -> Talks:
    """The right transport for how this one is configured."""
    return Remote(spec) if spec.remote else Server(spec)


# --- reading what came back ---------------------------------------------------


def _result(name: str, record: dict[str, Any]) -> dict[str, Any]:
    if "error" in record:
        problem = record["error"]
        raise Unreachable(f"{name}: {problem.get('message', problem)}")
    return record.get("result", {})


def _records(content_type: str, body: bytes) -> list[dict[str, Any]]:
    """Every JSON-RPC record in one HTTP answer.

    Streamable HTTP lets a server reply with a single JSON object or with an
    event stream carrying several. The stream is read whole here rather than
    incrementally: what is being waited for is one reply to one request, and a
    server that keeps the connection open after sending it would be answered by
    the read timeout rather than by a parser that never stops.
    """
    text = body.decode(errors="replace").strip()
    if not text:
        return []

    if "text/event-stream" in content_type:
        found = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                found.append(json.loads(line[5:].strip()))
            except ValueError:
                continue
        return [r for r in found if isinstance(r, dict)]

    try:
        one = json.loads(text)
    except ValueError:
        return []
    # A batch is legal and arrives as a list.
    if isinstance(one, list):
        return [r for r in one if isinstance(r, dict)]
    return [one] if isinstance(one, dict) else []


def _one_line(server: str, tool: str, args: dict[str, Any]) -> str:
    """The line in the tray: what, and roughly with what.

    Clipped hard, because an argument can be a whole program. `browser_evaluate`
    arrives carrying twenty lines of JavaScript, and `repr` of that is one line
    of escaped backslashes that nobody can read and nobody should be asked to
    approve. The full text is in the detail below it, laid out.
    """
    said = []
    for key, value in args.items():
        if isinstance(value, str):
            flat = " ".join(value.split())
            shown = flat if len(flat) <= 40 else f"{flat[:39]}…"
            said.append(f"{key}={shown!r}")
        else:
            rendered = repr(value)
            said.append(f"{key}={rendered if len(rendered) <= 40 else '…'}")
    return f"{server}: {tool}(" + ", ".join(said) + ")"


def _in_full(tool: str, args: dict[str, Any]) -> Detail | None:
    """The arguments as something a person can actually read.

    A `Body`, because for the calls that matter the argument *is* the decision.
    `browser_evaluate(function=...)` is a program about to run inside a page you
    are signed in to; approving it without reading it is approving nothing in
    particular. Newlines are newlines here rather than `\n`.

    Nothing for a call whose arguments fit on the line above - a card repeating
    what was just read is noise, and noise is what stops people reading.
    """
    if not args:
        return None
    if all(len(str(v)) <= 40 for v in args.values()):
        return None

    lines = []
    for key, value in args.items():
        text = value if isinstance(value, str) else repr(value)
        if "\n" in text or len(text) > 40:
            lines.append(f"{key}:")
            lines.extend(f"    {row}" for row in text.splitlines() or [""])
        else:
            lines.append(f"{key}: {text}")
    return Body(title=tool, text="\n".join(lines))


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
