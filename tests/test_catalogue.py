"""The list of servers worth knowing about.

What is worth testing here is not the contents - a list of names cannot be
checked without the network, and a suite that reaches the network fails on a
train. What is checked is that the entries are well formed and that the config
printed from them is config aven can actually read back.

The names themselves were verified against the registries and, where they could
be run, against the servers themselves. That is a thing done when the list
changes, not on every test run.
"""

import tomllib

from aven.toolkit.catalogue import CATALOGUE, Known, as_toml, printable
from aven.toolkit.connectors import build
from aven.harness.vault import Nowhere


def test_every_entry_says_how_to_reach_it_and_only_one_way():
    for known in CATALOGUE:
        assert bool(known.command) != bool(known.url), (
            f"{known.name}: a command or a url, never both and never neither"
        )


def test_every_entry_says_whose_it_is():
    """The interesting question about an MCP server is not what it does but
    whose computer it is. A community server is somebody else's code holding
    your credentials, and that has to be on screen."""
    for known in CATALOGUE:
        assert known.who in ("official", "community"), known.name


def test_no_entry_hands_a_stranger_a_google_account():
    """Searching for one returns a dozen packages by a dozen strangers. aven's
    answer for Google is its own connector, with an OAuth client the person
    registers themselves."""
    for known in CATALOGUE:
        if known.who == "community":
            assert "google" not in (known.about + known.name).lower()


def test_a_remote_entry_is_https():
    for known in CATALOGUE:
        if known.url:
            assert known.url.startswith("https://"), known.name


def test_what_is_printed_is_config_aven_can_read_back(tmp_path):
    """The whole point is pasting it. A listing that produced something the
    parser rejects would be found out one person at a time."""
    everything = "\n\n".join(as_toml(known) for known in CATALOGUE)
    (tmp_path / "connectors.toml").write_text(everything)

    parsed = tomllib.loads(everything)
    assert set(parsed["mcp"]) == {k.name for k in CATALOGUE}

    built, trouble = build(tmp_path, Nowhere())

    assert trouble == [], f"aven refused its own catalogue: {trouble}"
    assert {c.name for c in built} == {k.name for k in CATALOGUE}


def test_nothing_is_started_by_listing_or_parsing_it(tmp_path):
    """Six configured servers must not mean six processes launched because
    somebody asked what was available."""
    everything = "\n\n".join(as_toml(known) for known in CATALOGUE)
    (tmp_path / "connectors.toml").write_text(everything)

    built, _ = build(tmp_path, Nowhere())

    for connector in built:
        assert callable(connector.tools), f"{connector.name} resolved eagerly"


def test_the_listing_names_every_entry():
    said = printable()
    for known in CATALOGUE:
        assert known.name in said
        assert known.about in said


def test_suggested_risks_are_only_ever_read():
    """A catalogue may say "this one only reads". It must never pre-approve
    something that changes the world - that judgement is not a default's to
    make."""
    for known in CATALOGUE:
        assert all(isinstance(t, str) for t in known.read_only)
        block = as_toml(known)
        assert '"reversible"' not in block
        assert '"irreversible"' not in block


def test_a_tool_not_named_keeps_the_safe_default(tmp_path):
    """The suggestions are a shortlist, not a whitelist. Everything else waits."""
    known = next(k for k in CATALOGUE if k.read_only)
    (tmp_path / "connectors.toml").write_text(as_toml(known))

    built, _ = build(tmp_path, Nowhere())
    spec = built[0].close.__self__.spec  # the Server behind it

    assert set(spec.risk) == set(known.read_only)
    assert all(risk == "read" for risk in spec.risk.values())
    assert spec.trust is False, "and the server is not believed about the rest"
