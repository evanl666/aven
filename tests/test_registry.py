"""Searching the public MCP registry.

The network is stubbed at `_fetch`, which is the seam: everything worth testing
is what happens to a record after it arrives, and a suite that reaches the
registry is a suite that fails on a train and fails again the day somebody
publishes something.
"""

import json
import time

import pytest

from aven.toolkit import registry
from aven.toolkit.registry import Listing, Offline, as_toml, publisher_of, search

REMOTE = {
    "name": "com.stripe/mcp",
    "description": "Tools for customers, products, payments, and more.",
    "remotes": [{"type": "streamable-http", "url": "https://mcp.stripe.com"}],
}

LOCAL = {
    "name": "io.github.someone/notes",
    "description": "Notes in a folder.",
    "packages": [{
        "registryType": "npm",
        "identifier": "notes-mcp",
        "runtimeHint": "npx",
        "transport": {"type": "stdio"},
        "runtimeArguments": [{"value": "-y", "type": "positional"}],
        "environmentVariables": [
            {"name": "NOTES_TOKEN", "isRequired": True},
            {"name": "NOTES_DEBUG"},
        ],
    }],
}


@pytest.fixture
def answers(monkeypatch):
    """Whatever the registry is pretending to return, and how often it was asked."""
    held = {"rows": [REMOTE], "asked": 0, "offline": False}

    def fake_fetch(query, limit):
        held["asked"] += 1
        if held["offline"]:
            raise Offline("no network")
        return held["rows"]

    monkeypatch.setattr(registry, "_fetch", fake_fetch)
    return held


# --- who published it --------------------------------------------------------


def test_the_namespace_is_read_back_as_a_domain():
    """The registry verifies namespace ownership, so this is a fact rather than
    an endorsement - which is the whole reason it replaced a hand-written
    'official' label."""
    assert publisher_of("com.stripe/mcp") == "stripe.com"
    assert publisher_of("eu.nordicmcp/stripe") == "nordicmcp.eu"
    assert publisher_of("app.vercel.stripecheckup/x") == "stripecheckup.vercel.app"


def test_a_github_namespace_names_the_account_and_not_github():
    """`io.github.someone` is owned by that account, not by GitHub. Calling it
    "github.com" would be the exact confusion this is meant to prevent - it
    would make a stranger's server look like it came from a company."""
    assert publisher_of("io.github.someone/notes") == "github.com/someone"


# --- what comes back ---------------------------------------------------------


def test_a_remote_server_becomes_a_url(answers):
    found, _ = search("stripe", home=_home())

    assert found[0].url == "https://mcp.stripe.com"
    assert found[0].remote is True
    assert found[0].publisher == "stripe.com"


def test_a_package_becomes_a_command_with_its_runtime(answers, tmp_path):
    answers["rows"] = [LOCAL]

    found, _ = search("notes", home=tmp_path)

    assert found[0].command == "npx"
    assert found[0].args == ["-y", "notes-mcp"]
    assert found[0].remote is False


def test_required_environment_variables_are_named(answers, tmp_path):
    """So somebody pasting the config is not left to find them one failure at
    a time. Only the required ones - listing the optional ones as blanks would
    read as things that must be filled in."""
    answers["rows"] = [LOCAL]

    found, _ = search("notes", home=tmp_path)

    assert found[0].needs == ["NOTES_TOKEN"]


def test_a_server_with_no_way_to_run_it_still_lists(answers, tmp_path):
    """The registry holds records with neither. Dropping them would hide that
    the thing exists; the listing says there is nothing to paste."""
    answers["rows"] = [{"name": "com.x/y", "description": "Nothing runnable."}]

    found, _ = search("x", home=tmp_path)

    assert found[0].command == "" and found[0].url == ""
    assert "nothing to paste" in registry.found("x", found, "live")


# --- the local name ----------------------------------------------------------


def test_the_suggested_name_is_short_because_every_tool_carries_it():
    """`com.stripe/mcp` as a local name would make every tool
    `com_stripe_mcp_create_customer`, in every request, for as long as the
    group is loaded."""
    assert Listing(name="com.stripe/mcp", description="", publisher="").suggested == "stripe"
    assert Listing(name="io.github.a/notes", description="", publisher="").suggested == "notes"
    assert Listing(name="com.x/mcp-server", description="", publisher="").suggested == "x"


def test_a_suggested_name_is_always_usable_as_a_toml_key():
    made = Listing(name="com.foo/bar-baz.qux", description="", publisher="").suggested

    assert made.replace("_", "").isalnum()


# --- the cache ---------------------------------------------------------------


def _home():
    import tempfile

    return tempfile.mkdtemp()


def test_the_same_search_twice_asks_the_registry_once(answers, tmp_path):
    search("stripe", home=tmp_path)
    found, where = search("stripe", home=tmp_path)

    assert answers["asked"] == 1
    assert where == "cached"
    assert found[0].name == "com.stripe/mcp"


def test_a_stale_cache_is_asked_again(answers, tmp_path):
    search("stripe", home=tmp_path)

    search("stripe", home=tmp_path, fresh=-1)

    assert answers["asked"] == 2


def test_no_network_falls_back_to_what_was_cached_and_says_how_old(answers, tmp_path):
    """Knowing a name from yesterday beats knowing nothing, but only if the
    reader is told which it is."""
    search("stripe", home=tmp_path)
    answers["offline"] = True

    found, where = search("stripe", home=tmp_path, fresh=-1)

    assert found[0].name == "com.stripe/mcp"
    assert "cached" in where and "ago" in where


def test_no_network_and_nothing_cached_says_so_rather_than_pretending(answers, tmp_path):
    answers["offline"] = True

    with pytest.raises(Offline):
        search("stripe", home=tmp_path)


def test_a_corrupt_cache_is_ignored_rather_than_fatal(answers, tmp_path):
    (tmp_path / registry.CACHE).write_text("not json{{{")

    found, where = search("stripe", home=tmp_path)

    assert found and where == "live"


def test_a_cache_that_cannot_be_written_is_only_a_slower_search(answers, tmp_path):
    """A read-only home directory is a real thing. It must not stop a search."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("")

    found, _ = search("stripe", home=blocked / "under")

    assert found


# --- what gets pasted --------------------------------------------------------


def test_the_printed_config_is_toml_aven_can_read_back(answers, tmp_path):
    """The whole point is that somebody pastes it. A listing that printed
    something the config reader rejects would be found out one person at a
    time."""
    import tomllib

    from aven.harness.vault import Nowhere
    from aven.toolkit.connectors import build

    answers["rows"] = [REMOTE, LOCAL]
    found, _ = search("anything", home=tmp_path)
    everything = "\n\n".join(as_toml(one) for one in found)

    parsed = tomllib.loads(everything)
    assert set(parsed["mcp"]) == {"stripe", "notes"}

    (tmp_path / "connectors.toml").write_text(everything)
    built, trouble = build(tmp_path, Nowhere())

    assert trouble == [], f"aven refused what it printed: {trouble}"
    assert {c.name for c in built} == {"stripe", "notes"}


def test_a_description_with_a_quote_in_it_does_not_break_the_config(answers, tmp_path):
    import tomllib

    answers["rows"] = [{
        "name": "com.x/y",
        "description": 'It says "hello" and\nspans lines.',
        "remotes": [{"type": "streamable-http", "url": "https://x/mcp"}],
    }]
    found, _ = search("x", home=tmp_path)

    parsed = tomllib.loads(as_toml(found[0]))

    assert "hello" in parsed["mcp"]["y"]["about"]


def test_the_risk_block_is_commented_out_and_suggests_nothing(answers, tmp_path):
    """A listing may say where to write your judgement. It may not write one:
    the safe default is that every tool waits, and a printed default would be
    this program deciding for somebody about a server it has never run."""
    found, _ = search("stripe", home=tmp_path)
    block = as_toml(found[0])

    assert "# [mcp.stripe.risk]" in block
    assert '\nsome_tool = "read"' not in block, "uncommented, it would take effect"


def test_nothing_found_says_so_rather_than_printing_an_empty_list(answers, tmp_path):
    answers["rows"] = []

    found, where = search("nonsense", home=tmp_path)

    assert found == []
    assert "Nothing in the registry" in registry.found("nonsense", found, where)


# --- a registry that is slow and flaky ---------------------------------------


def test_one_failure_is_retried_rather_than_reported(monkeypatch, tmp_path):
    """Measured, not imagined: three consecutive requests to the registry took
    25s (timeout), 17.5s (answered) and 25s (timeout), from a machine where
    github.com answered in a tenth of a second. One attempt would report a
    working registry as down two times in three."""
    import urllib.error

    tries = []

    class Once:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def flaky(request, timeout=None):
        tries.append(timeout)
        if len(tries) == 1:
            raise TimeoutError("the read operation timed out")
        return Once({"servers": [{"server": REMOTE}]})

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    monkeypatch.setattr(registry.time, "sleep", lambda _: None)

    found, where = search("stripe", home=tmp_path)

    assert len(tries) == 2, "it gave up after one"
    assert found[0].name == "com.stripe/mcp"
    assert where == "live"


def test_it_gives_up_rather_than_retrying_forever(monkeypatch, tmp_path):
    tries = []

    def never(request, timeout=None):
        tries.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", never)
    monkeypatch.setattr(registry.time, "sleep", lambda _: None)

    with pytest.raises(Offline):
        search("stripe", home=tmp_path)

    assert len(tries) == registry.TRIES


def test_the_timeout_leaves_room_for_a_slow_answer():
    """17.5s was a real successful response. A limit below that would turn the
    registry's good days into failures."""
    assert registry.PATIENCE > 17.5
