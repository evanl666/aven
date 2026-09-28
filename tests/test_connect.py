"""Connectors: the states they are in, and what the model may do about them."""

import pytest

from aven.harness.connect import Connections, Connector
from aven.harness.toolbox import ToolBox
from aven.harness.tools import tool


@tool(risk="read")
def look() -> str:
    """Look."""
    return "looked"


@tool(risk="read")
def check() -> str:
    """Check."""
    return "checked"


class Signin:
    """An auth that can be told whether to succeed."""

    def __init__(self, already: bool = False, fails: str | None = None) -> None:
        self.held = already
        self.fails = fails
        self.attempts = 0

    def ready(self) -> bool:
        return self.held

    def sign_in(self) -> None:
        self.attempts += 1
        if self.fails:
            raise RuntimeError(self.fails)
        self.held = True

    def forget(self) -> bool:
        was, self.held = self.held, False
        return was

    def about(self) -> str:
        return "somewhere"


def test_a_connector_with_nothing_to_sign_in_to_is_ready_at_once():
    """The local ones - files, memory, the calendar on this very machine - have
    nothing to authorise against, and a sign-in that always succeeds would be
    ceremony pretending to be security."""
    connections = Connections([Connector(name="memory", about="", tools=[look])])

    assert connections.state("memory") == "ready"
    assert connections.gate("memory") is None


def test_one_that_needs_signing_in_says_so_until_it_is():
    auth = Signin()
    connections = Connections([Connector(name="g", about="", tools=[look], auth=auth)])

    assert connections.state("g") == "needs_sign_in"
    connections.sign_in("g")
    assert connections.state("g") == "ready"


def test_a_failed_sign_in_is_recorded_and_raised():
    """Both, because they are read in different places: the raise tells the
    caller it did not work, and the record is what the pane draws next to the
    row instead of silently flipping back to a button."""
    connector = Connector(name="g", about="", tools=[look], auth=Signin(fails="you said no"))
    connections = Connections([connector])

    with pytest.raises(RuntimeError, match="you said no"):
        connections.sign_in("g")

    assert connector.trouble == "you said no"
    assert connections.state("g") == "needs_sign_in", "and not left spinning"


def test_a_second_attempt_clears_the_last_complaint():
    auth = Signin(fails="no")
    connector = Connector(name="g", about="", tools=[look], auth=auth)
    connections = Connections([connector])
    with pytest.raises(RuntimeError):
        connections.sign_in("g")

    auth.fails = None
    connections.sign_in("g")

    assert connector.trouble is None


def test_the_model_cannot_load_a_service_nobody_signed_in_to():
    """`use_tools` on an unsigned service must come back with a sentence it can
    act on. Loading the tools would give it things to call that fail on every
    call, and nothing to tell the person."""
    connections = Connections([
        Connector(name="g", about="", tools=[look], auth=Signin()),
    ])
    box = ToolBox(core=[], groups=connections.groups(), gate=connections.gate)

    opener = next(t for t in box.active() if t.name == "use_tools")
    said = opener(group="g").output

    assert "not connected" in said
    assert "g" not in box.brought_in
    assert not any(t.name == "look" for t in box.active())


def test_once_signed_in_the_model_may_load_it():
    auth = Signin()
    connections = Connections([Connector(name="g", about="", tools=[look], auth=auth)])
    box = ToolBox(core=[], groups=connections.groups(), gate=connections.gate)
    connections.sign_in("g")

    opener = next(t for t in box.active() if t.name == "use_tools")
    said = opener(group="g").output

    assert "loaded g" in said
    assert any(t.name == "look" for t in box.active())


def test_a_group_that_is_a_function_is_not_called_until_it_is_needed():
    """Resolving an MCP connector is what starts its server process. Six
    configured servers must not mean six processes launched to answer one
    question about a file."""
    built = []

    def make():
        built.append(1)
        return [check]

    box = ToolBox(core=[], groups={"lazy": make})

    assert built == [], "merely having it must cost nothing"
    box.active()
    assert built == [], "nor listing what is dormant"

    box.bring_in("lazy")
    assert any(t.name == "check" for t in box.active())
    assert built, "and now it has been built"


def test_putting_a_group_away_takes_its_tools_out_of_play():
    connections = Connections([Connector(name="g", about="", tools=[look])])
    box = ToolBox(core=[], groups=connections.groups(), gate=connections.gate)
    box.bring_in("g")

    assert box.put_away("g") is True
    assert box.put_away("g") is False, "nothing to put away twice"
    assert not any(t.name == "look" for t in box.active())


def test_forgetting_a_credential_puts_the_connector_back_to_needing_one():
    auth = Signin(already=True)
    connections = Connections([Connector(name="g", about="", tools=[look], auth=auth)])

    assert connections.forget("g") is True
    assert connections.state("g") == "needs_sign_in"


def test_forgetting_something_with_no_credential_is_not_an_error():
    connections = Connections([Connector(name="memory", about="", tools=[look])])

    assert connections.forget("memory") is False


def test_disconnecting_lets_go_of_whatever_was_running_behind_it():
    """A connector that runs its tools in another process has to be told when
    nobody wants them any more. In a window the agent outlives many
    conversations, so connecting and disconnecting a few times over an
    afternoon would leave a few processes behind."""
    closed = []
    connections = Connections([
        Connector(name="mcp", about="", tools=[look], close=lambda: closed.append(1))
    ])

    connections.release("mcp")

    assert closed == [1]


def test_letting_go_of_a_local_connector_is_not_an_error():
    """Most have nothing running behind them."""
    connections = Connections([Connector(name="memory", about="", tools=[look])])

    connections.release("memory")  # no close, nothing to do
    connections.release("nope")    # and an unknown name is not a crash


def test_something_that_will_not_shut_down_does_not_fail_the_disconnect():
    """The person asked to disconnect. Failing that because the thing behind it
    would not leave cleanly leaves them with the tools still in play and no way
    to try again."""
    def stubborn():
        raise OSError("will not go")

    connections = Connections([
        Connector(name="mcp", about="", tools=[look], close=stubborn)
    ])

    connections.release("mcp")  # does not raise


def test_shutting_down_lets_go_of_all_of_them():
    closed = []
    connections = Connections([
        Connector(name="a", about="", tools=[look], close=lambda: closed.append("a")),
        Connector(name="b", about="", tools=[look], close=lambda: closed.append("b")),
        Connector(name="local", about="", tools=[look]),
    ])

    connections.release_all()

    assert sorted(closed) == ["a", "b"]
