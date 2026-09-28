"""Where a connector's credentials live.

A token that lets something act as you on your mail or your calendar is not
configuration. It does not belong in a dotfile beside the settings, it must not
reach a log or a session transcript, and it must survive the process that
fetched it without being readable by every other program you run.

macOS already has somewhere for this, so on macOS that is where it goes: the
login keychain, through `security`, which is the same place Safari and the
system tools keep theirs. Access is then governed by the OS rather than by this
code being careful.

Everywhere else there is no such place, so there is a file - and the file says
so out loud. `Vault.about()` is not decoration: a person deciding whether to
sign a service in is entitled to know whether the token lands in a keychain or
in their home directory, and a product that quietly did the weaker thing on one
platform would be lying by omission.

Nothing here is encrypted by aven itself. Encryption needs a key, the key needs
somewhere to live, and somewhere to live is the problem being solved - so a
home-made scheme would mean a second copy of the same question plus the chance
of getting it wrong. The OS keychain or file permissions, honestly labelled.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Protocol


class Vault(Protocol):
    """Somewhere secrets can be put, fetched and forgotten, by name."""

    def get(self, name: str) -> dict[str, Any] | None: ...

    def put(self, name: str, secret: dict[str, Any]) -> None: ...

    def forget(self, name: str) -> bool: ...

    def about(self) -> str:
        """Where this puts things, in one line, for a person to read."""
        ...


class Keychain:
    """The macOS login keychain, via `security`.

    A generic password per connector, with the JSON as the password itself.
    Keyed by service name so it is findable in Keychain Access by hand - the
    person owns these and must be able to revoke one without this program's
    help.
    """

    def __init__(self, prefix: str = "aven") -> None:
        self.prefix = prefix

    def _service(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def get(self, name: str) -> dict[str, Any] | None:
        found = subprocess.run(
            ["security", "find-generic-password", "-s", self._service(name), "-w"],
            capture_output=True,
            text=True,
        )
        # Exit 44 is "no such item", which is not an error - it is a connector
        # nobody has signed in to yet.
        if found.returncode != 0:
            return None
        try:
            held = json.loads(found.stdout.strip())
        except ValueError:
            # Something else wrote under this name, or it was edited by hand into
            # nonsense. Treating it as absent is what lets signing in again fix
            # it, where raising would leave the connector permanently stuck.
            return None
        return held if isinstance(held, dict) else None

    def put(self, name: str, secret: dict[str, Any]) -> None:
        # -U updates in place if it is already there. Without it a second sign-in
        # fails rather than replacing the token it just refreshed.
        written = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", self._service(name),
                "-a", self.prefix,
                "-w", json.dumps(secret),
                "-U",
            ],
            capture_output=True,
            text=True,
        )
        if written.returncode != 0:
            raise RuntimeError(written.stderr.strip() or "could not write to the keychain")

    def forget(self, name: str) -> bool:
        gone = subprocess.run(
            ["security", "delete-generic-password", "-s", self._service(name)],
            capture_output=True,
            text=True,
        )
        return gone.returncode == 0

    def about(self) -> str:
        return "the macOS login keychain"


class Locked:
    """A file only its owner can read, for platforms with no keychain.

    Weaker than a keychain and labelled as such. The mode is set before anything
    is written, not after: a file that spends even a moment at the default 644
    with a refresh token in it has already been readable by every process on the
    machine.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _all(self) -> dict[str, Any]:
        try:
            held = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return held if isinstance(held, dict) else {}

    def _write(self, everything: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Opened by descriptor with the mode in the call, so it is never created
        # readable and then narrowed.
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(everything, file)
        os.chmod(self.path, 0o600)  # in case it existed already, wider

    def get(self, name: str) -> dict[str, Any] | None:
        held = self._all().get(name)
        return held if isinstance(held, dict) else None

    def put(self, name: str, secret: dict[str, Any]) -> None:
        everything = self._all()
        everything[name] = secret
        self._write(everything)

    def forget(self, name: str) -> bool:
        everything = self._all()
        if name not in everything:
            return False
        del everything[name]
        self._write(everything)
        return True

    def about(self) -> str:
        return f"{self.path} (owner-readable only - this platform has no keychain)"


class Nowhere:
    """A vault that keeps nothing, for tests and for a run that must not persist.

    Not a null object that silently swallows writes: it holds them in memory for
    the life of the process, so a connector signed in during a run stays signed
    in for that run and is gone afterwards.
    """

    def __init__(self) -> None:
        self.held: dict[str, dict[str, Any]] = {}

    def get(self, name: str) -> dict[str, Any] | None:
        return self.held.get(name)

    def put(self, name: str, secret: dict[str, Any]) -> None:
        self.held[name] = dict(secret)

    def forget(self, name: str) -> bool:
        return self.held.pop(name, None) is not None

    def about(self) -> str:
        return "memory only - nothing is kept after this run"


def vault_for(home: Path | None = None) -> Vault:
    """The best place this machine has.

    The keychain is only claimed if `security` is actually there. A machine that
    has macOS but not the tool - a stripped container, a locked-down build - must
    fall back rather than fail every write.
    """
    base = Path(home) if home is not None else Path.home() / ".aven"
    if platform.system() == "Darwin":
        found = subprocess.run(["which", "security"], capture_output=True, text=True)
        if found.returncode == 0:
            return Keychain()
    return Locked(base / "credentials.json")
