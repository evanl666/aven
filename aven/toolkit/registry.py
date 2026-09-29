"""The public MCP registry, searched and cached.

This replaced a list written by hand. Writing one taught me why: checking nine
entries against the package registries turned up four deprecated and one that
had never existed, and that was on the day it was written. A list maintained by
hand is a list that is wrong later, and the registry has some nine thousand
entries and an API.

## Who published it, rather than whether it is blessed

The hand-written list carried an `official` / `community` label, which was my
judgement asserted as a fact. The registry has something better and duller: the
name is a reverse-DNS namespace whose ownership the registry verifies.

    com.stripe/mcp                 published by whoever controls stripe.com
    eu.nordicmcp/stripe            published by nordicmcp.eu - not Stripe
    io.github.someone/stripe-tool  published by that GitHub account

So nothing here says "official". It says **stripe.com** or **nordicmcp.eu**, and
whether that is the party you meant to trust is a question only the person
asking can answer. A third-party server is not worse - it is different, and the
difference is one a label would have hidden.

## Cached, and honest about it

A search that needs the network is a search that fails on a train. Results are
kept for a day, and a stale answer is served with its age rather than withheld:
knowing a name from yesterday beats knowing nothing.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WHERE = "https://registry.modelcontextprotocol.io/v0/servers"
CACHE = "registry.json"

# A day. Long enough that repeated looking costs nothing, short enough that a
# server published this morning is findable this afternoon.
FRESH = 24 * 60 * 60


class Offline(Exception):
    """The registry could not be reached and nothing was cached."""


@dataclass(frozen=True)
class Listing:
    """One server, in the shape the config file wants."""

    name: str
    description: str
    publisher: str

    # Reached one way or the other, exactly as with a hand-written entry.
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)

    # Environment variables the server says it cannot work without. Named so
    # somebody pasting the config is not left to discover them one failure at
    # a time.
    needs: list[str] = field(default_factory=list)

    @property
    def remote(self) -> bool:
        return bool(self.url)

    @property
    def suggested(self) -> str:
        """A short local name for the `[mcp.<name>]` table.

        The registry's names are long and dotted - `com.stripe/mcp` - and the
        local name is what every tool of this server gets prefixed with. The
        last meaningful word keeps `stripe_create_customer` from becoming
        `com_stripe_mcp_create_customer`.
        """
        tail = self.name.split("/")[-1]
        if tail in ("mcp", "server", "mcp-server") and "/" in self.name:
            tail = self.name.split("/")[0].split(".")[-1]
        return "".join(c if c.isalnum() else "_" for c in tail).strip("_").lower()


def publisher_of(name: str) -> str:
    """Who the registry verified as the owner of this namespace.

    A reverse-DNS namespace read back the right way round. `io.github.x/y` is
    left as a GitHub account rather than being called "github.com", because the
    owner is the account and saying otherwise would be the exact confusion this
    is meant to prevent.
    """
    space = name.split("/")[0]
    parts = space.split(".")
    if len(parts) >= 3 and parts[0] == "io" and parts[1] == "github":
        return f"github.com/{parts[2]}"
    return ".".join(reversed(parts))


def search(
    query: str, *, home: Path, limit: int = 20, fresh: float = FRESH
) -> tuple[list[Listing], str]:
    """Search the registry. Returns what was found and where it came from.

    The second value is for saying so out loud: results half a day old are
    worth having and worth labelling, and neither is true of results presented
    as if they had just arrived.
    """
    kept = _cached(home)
    key = f"{query}|{limit}"
    held = kept.get(key)

    if held and time.time() - held.get("at", 0) < fresh:
        return [_listing(row) for row in held["rows"]], "cached"

    try:
        rows = _fetch(query, limit)
    except Offline:
        if held:
            age = time.time() - held.get("at", 0)
            return [_listing(r) for r in held["rows"]], f"cached {_ago(age)} ago"
        raise

    kept[key] = {"at": time.time(), "rows": rows}
    _keep(home, kept)
    return [_listing(row) for row in rows], "live"


def _fetch(query: str, limit: int) -> list[dict[str, Any]]:
    asking = urllib.parse.urlencode({
        "search": query,
        "limit": limit,
        # Without this the same server comes back once per published version.
        "version": "latest",
    })
    request = urllib.request.Request(
        f"{WHERE}?{asking}", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            body = json.loads(answer.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as unreachable:
        raise Offline(str(unreachable)) from unreachable

    return [row["server"] for row in body.get("servers", []) if "server" in row]


def _listing(server: dict[str, Any]) -> Listing:
    name = str(server.get("name", ""))

    remote = ""
    for far in server.get("remotes") or []:
        if far.get("type") in ("streamable-http", "sse") and far.get("url"):
            remote = str(far["url"])
            break

    command, args, needs = "", [], []
    for package in server.get("packages") or []:
        if (package.get("transport") or {}).get("type") not in (None, "stdio"):
            continue
        runtime = str(package.get("runtimeHint") or "")
        identifier = str(package.get("identifier") or "")
        if not runtime or not identifier:
            continue
        command = runtime
        args = [
            str(a.get("value"))
            for a in (package.get("runtimeArguments") or [])
            if a.get("value")
        ] + [identifier]
        needs = [
            str(v.get("name"))
            for v in (package.get("environmentVariables") or [])
            if v.get("isRequired") and v.get("name")
        ]
        break

    return Listing(
        name=name,
        description=str(server.get("description", "")).strip(),
        publisher=publisher_of(name),
        url=remote,
        command=command,
        args=args,
        needs=needs,
    )


# --- printing it ------------------------------------------------------------


def as_toml(listing: Listing, called: str = "") -> str:
    """One listing, as the lines to paste into connectors.toml."""
    local = called or listing.suggested
    lines = [f"[mcp.{local}]"]
    if listing.remote:
        lines.append(f'url = "{listing.url}"')
    else:
        lines.append(f'command = "{listing.command}"')
        listed = ", ".join(f'"{a}"' for a in listing.args)
        lines.append(f"args = [{listed}]")
    lines.append(f'about = "{_quotable(listing.description)}"')

    if listing.needs:
        lines.append("")
        lines.append(f"[mcp.{local}.env]")
        for name in listing.needs:
            lines.append(f'{name} = ""    # required')

    lines.append("")
    lines.append("# Every tool of this server waits for your approval until you")
    lines.append("# say otherwise. Run it once, read what each one asks to do,")
    lines.append("# then name the harmless ones here:")
    lines.append(f"# [mcp.{local}.risk]")
    lines.append(f'# some_tool = "read"')
    return "\n".join(lines)


def found(query: str, listings: list[Listing], where: str, width: int = 78) -> str:
    """What `aven --connectors <query>` prints."""
    if not listings:
        return (
            f'Nothing in the registry matches "{query}".\n'
            "Try a shorter word - the search matches names and descriptions."
        )

    out = [
        f'{len(listings)} server{"" if len(listings) == 1 else "s"} matching '
        f'"{query}" ({where}).',
        "",
        "The name is a namespace the registry checked the ownership of, so the",
        "publisher below is a fact rather than an endorsement. Whether it is the",
        "party you meant is yours to judge.",
        "",
    ]
    for listing in listings:
        out.append("-" * width)
        out.append(f"{listing.name}")
        out.append(f"published by {listing.publisher}")
        if listing.description:
            out.append(listing.description)
        if not listing.remote and not listing.command:
            out.append("(no way to run it is published - nothing to paste)")
            out.append("")
            continue
        out.append("")
        out.append(as_toml(listing))
        out.append("")
    out.append("-" * width)
    out.append("Paste one into ~/.aven/connectors.toml, then connect it.")
    return "\n".join(out)


def _quotable(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")[:200]


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 86400:
        return f"{seconds / 3600:.0f} hours"
    return f"{seconds / 86400:.0f} days"


# --- the cache --------------------------------------------------------------


def _cached(home: Path) -> dict[str, Any]:
    try:
        held = json.loads((Path(home) / CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def _keep(home: Path, everything: dict[str, Any]) -> None:
    path = Path(home) / CACHE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(everything), encoding="utf-8")
    except OSError:
        # A cache that cannot be written is a slower search, not a failure.
        pass
