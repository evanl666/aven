"""Services this run could use, and which of them are signed in.

`ToolBox` answers "which tools may the model call this turn" and should keep
answering only that. Signing in is a different question with a different shape:
it can fail, it can take thirty seconds while somebody clicks through a consent
screen in a browser, and its answer outlives the process. So it lives here, and
hands the box a plain group when it is done.

Three states, because a person looking at a list of services needs to tell them
apart:

    ready          signed in, or nothing to sign in to. Its tools work.
    needs_sign_in  known, described, and one click away from working.
    signing_in     a browser is open and somebody is deciding.

A connector with `auth=None` is `ready` from the start. That is not a special
case bolted on: the local ones - files, memory, the calendar on this very
machine - have nothing to authorise against, and forcing them through a sign-in
that always succeeds would be ceremony pretending to be security.

The tools are a `ToolSource`, so they may be a function. A connector whose tools
need a token builds them when it is brought in rather than at startup, which is
what stops an unsigned connector from putting tools in front of the model that
cannot work.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from aven.harness.toolbox import ToolSource

State = Literal["ready", "needs_sign_in", "signing_in"]


class Auth(Protocol):
    """What a connector needs in order to be signed in to."""

    def ready(self) -> bool:
        """Whether there is a usable credential right now."""
        ...

    def sign_in(self) -> None:
        """Get one. Blocks, and raises with a readable reason if it fails."""
        ...

    def forget(self) -> bool:
        """Throw the credential away. True if there was one."""
        ...

    def about(self) -> str:
        """Where the credential is kept, in one line."""
        ...


@dataclass
class Connector:
    """One service, its tools, and how to sign in to it if it needs that."""

    name: str
    about: str
    tools: ToolSource
    auth: Auth | None = None

    # Set when the last sign-in attempt failed, so the surface can say why
    # rather than just flipping back to a button.
    trouble: str | None = field(default=None, compare=False)


class Connections:
    """The connectors this run knows about, and what state each is in."""

    def __init__(self, connectors: Sequence[Connector] = ()) -> None:
        self.held: dict[str, Connector] = {c.name: c for c in connectors}
        self.working: set[str] = set()

    def __contains__(self, name: object) -> bool:
        return name in self.held

    def __iter__(self):
        return iter(self.held.values())

    def get(self, name: str) -> Connector | None:
        return self.held.get(name)

    def groups(self) -> dict[str, ToolSource]:
        """What to hand `ToolBox`: every connector, by name."""
        return {c.name: c.tools for c in self.held.values()}

    def describe(self) -> dict[str, str]:
        return {c.name: c.about for c in self.held.values()}

    def state(self, name: str) -> State:
        connector = self.held.get(name)
        if connector is None or connector.auth is None:
            return "ready"
        if name in self.working:
            return "signing_in"
        return "ready" if connector.auth.ready() else "needs_sign_in"

    def ready(self, name: str) -> bool:
        return self.state(name) == "ready"

    def sign_in(self, name: str) -> None:
        """Sign one in, blocking. Raises with a readable reason if it fails.

        The `working` marker is set before and cleared after whatever happens,
        so a surface polling `state()` from another thread sees `signing_in` for
        exactly as long as somebody is actually deciding - and a sign-in that
        throws does not leave the row spinning forever.
        """
        connector = self.held.get(name)
        if connector is None:
            raise KeyError(f"no connector called {name!r}")
        if connector.auth is None:
            return

        self.working.add(name)
        connector.trouble = None
        try:
            connector.auth.sign_in()
        except Exception as problem:
            connector.trouble = str(problem) or type(problem).__name__
            raise
        finally:
            self.working.discard(name)

    def forget(self, name: str) -> bool:
        """Sign out. The tools stay in the box - they simply stop working, and
        the next call says so, which is more honest than pretending the
        conversation never had them."""
        connector = self.held.get(name)
        if connector is None or connector.auth is None:
            return False
        connector.trouble = None
        return connector.auth.forget()

    def gate(self, name: str) -> str | None:
        """Why the model may not bring this group in, if it may not.

        Handed to `ToolBox` so `use_tools` on an unsigned service comes back with
        a sentence the model can act on - telling the person it needs connecting
        - instead of loading tools that fail on every call and leaving it to
        guess why.
        """
        if name not in self.held:
            return None
        if self.state(name) == "ready":
            return None
        return (
            f"{name} is not connected yet. Tell the person it needs connecting "
            "in the Connections panel; you cannot do it for them."
        )
