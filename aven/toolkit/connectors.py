"""What `~/.aven/connectors.toml` says, turned into connectors.

A fourth file beside `approvals.toml`, `triggers.toml` and `desktop.toml`, and
for the same reason as the other three: the things a person is entitled to
change without this program's help belong in a file they can read.

    # ~/.aven/connectors.toml

    [google]
    client_id = "....apps.googleusercontent.com"
    client_secret = "...."

    [mcp.files]
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Notes"]
    about = "Read and write the notes folder"

    [mcp.files.risk]
    read_text_file = "read"      # your judgement, not the server's

Nothing here is required. A file that does not exist means no connectors beyond
the ones the app builds itself, which is a working assistant - every service is
opt-in, and the absence of a config is the absence of opt-in rather than an
error to report.

A broken file is not the same as an absent one and is not treated as one. A typo
that silently disabled a connector would be found out later, in the middle of
asking for something - so it is said plainly at startup and the rest still
loads.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aven.harness.connect import Connector
from aven.harness.vault import Vault
from aven.toolkit.google import calendar_auth, calendar_tools
from aven.toolkit.mcp import Spec, open_server

CONFIG = "connectors.toml"

GOOGLE_ABOUT = (
    "Google Calendar: read what is scheduled and add events. Take it when they "
    "mention their calendar, a meeting, or when something is happening."
)


def read(home: Path) -> tuple[dict[str, Any], list[str]]:
    """The file, and anything wrong with it.

    Returns what parsed plus a list of complaints, rather than raising. One
    mistyped server should not take the other connectors down with it.
    """
    path = Path(home) / CONFIG
    if not path.exists():
        return {}, []
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), []
    except (OSError, tomllib.TOMLDecodeError) as broken:
        return {}, [f"{path}: {broken}"]


def build(
    home: Path, vault: Vault, extra: Sequence[Connector] = ()
) -> tuple[list[Connector], list[str]]:
    """Every connector this machine is configured for.

    `extra` is whatever the app builds itself - the local ones, which need no
    configuration because there is nothing to sign in to. They come first, so a
    config file cannot quietly replace the calendar on this machine with
    something else answering to the same name.
    """
    said, trouble = read(home)
    built: list[Connector] = list(extra)
    taken = {c.name for c in built}

    for name, made in (
        *_google(said.get("google"), vault),
        *_mcp(said.get("mcp")),
    ):
        if isinstance(made, str):
            trouble.append(made)
            continue
        if name in taken:
            trouble.append(f"{name}: already built in, so the config for it is ignored")
            continue
        taken.add(name)
        built.append(made)

    return built, trouble


def append(home: Path, listing: Any, called: str = "") -> str:
    """Write one more `[mcp.*]` table into connectors.toml, and say what it is called.

    Appended as text rather than parsed, edited and re-serialised. A round trip
    through a TOML writer would come back without the comments, and the comments
    are half of why this file is one a person can keep - they say what each
    setting is for and that the risks are theirs to change.
    """
    from aven.toolkit.registry import as_toml

    local = (called or listing.suggested).strip()
    if not local or not all(c.isalnum() or c == "_" for c in local):
        raise ValueError(f"{local!r} is not usable as a name")

    said, _ = read(home)
    if local in (said.get("mcp") or {}):
        raise ValueError(f"there is already a connector called {local!r}")

    path = Path(home) / CONFIG
    block = as_toml(listing, local)
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    if before and not before.endswith("\n"):
        before += "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{before}\n# {listing.name} - published by {listing.publisher}\n{block}\n",
        encoding="utf-8",
    )
    return local


def one_mcp(name: str, settings: dict[str, Any]) -> Connector:
    """A single connector from one already-parsed `[mcp.<name>]` table.

    Split out so a connector can be added while aven is running, taking the same
    path as one read at startup. Two ways of building the same thing would drift,
    and the one that drifted would be the one nobody tested.
    """
    made = _mcp({name: settings})
    if not made or isinstance(made[0][1], str):
        raise ValueError(made[0][1] if made else f"mcp.{name}: nothing to build")
    return made[0][1]


def _google(said: Any, vault: Vault) -> list[tuple[str, Connector | str]]:
    if not isinstance(said, dict):
        return []
    client_id = str(said.get("client_id", "")).strip()
    if not client_id:
        return [("google", "google: client_id is missing")]

    auth = calendar_auth(client_id, str(said.get("client_secret", "")), vault)
    return [(
        "google",
        # The tools are a function of the auth, built when the group is brought
        # in. Building them now would mean holding an access token from before
        # anybody signed in.
        Connector(
            name="google",
            about=GOOGLE_ABOUT,
            tools=lambda: calendar_tools(auth),
            auth=auth,
        ),
    )]


def _mcp(said: Any) -> list[tuple[str, Connector | str]]:
    if not isinstance(said, dict):
        return []

    out: list[tuple[str, Connector | str]] = []
    for name, settings in said.items():
        if not isinstance(settings, dict):
            out.append((name, f"mcp.{name}: expected a table"))
            continue
        command = str(settings.get("command", "")).strip()
        url = str(settings.get("url", "")).strip()
        if command and url:
            out.append((name, f"mcp.{name}: give it a command or a url, not both"))
            continue
        if not command and not url:
            out.append((name, f"mcp.{name}: needs a command or a url"))
            continue
        # http:// to somewhere that is not this machine would put whatever the
        # tools carry, and the token authorising them, on the wire in clear.
        if url and not (url.startswith("https://") or url.startswith("http://127.0.0.1")
                        or url.startswith("http://localhost")):
            out.append((name, f"mcp.{name}: a remote url has to be https"))
            continue

        spec = Spec(
            name=str(name),
            command=command,
            args=[str(a) for a in settings.get("args", [])],
            env={str(k): str(v) for k, v in (settings.get("env") or {}).items()},
            cwd=str(settings["cwd"]) if settings.get("cwd") else None,
            url=url,
            headers={str(k): str(v) for k, v in (settings.get("headers") or {}).items()},
            about=str(settings.get("about", "")) or f"Tools from the {name} MCP server.",
            trust=bool(settings.get("trust", False)),
            risk={
                str(k): v
                for k, v in (settings.get("risk") or {}).items()
                if v in ("read", "reversible", "irreversible")
            },
        )
        server = open_server(spec)
        out.append((
            spec.name,
            # `tools` is the bound method, so the process starts when the group
            # is brought in rather than at startup. Six configured servers must
            # not mean six processes launched to answer one question about a
            # file. There is nothing to sign in to, so no auth.
            Connector(
                name=spec.name,
                about=spec.about,
                tools=server.tools,
                close=server.stop,
            ),
        ))
    return out
