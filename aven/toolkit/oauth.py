"""Signing in to a service that speaks OAuth, from a program with no server.

The loopback flow, which is what a desktop app is supposed to use: open the
provider's consent page in the person's own browser, listen on 127.0.0.1 for the
redirect it sends back, trade the code it carries for tokens. The person types
their password into the provider's page, in their own browser, and this program
never sees it - which is the entire point and the reason not to invent something
simpler.

Four details here are security rather than plumbing, and all four are the kind
that look like overhead until they are not:

**PKCE.** A desktop app cannot keep a client secret - it ships on the person's
machine and can be read straight out of the binary. So the authorization code
is bound to a one-time secret this process generates and only it knows: anything
that intercepts the code cannot spend it. Mandatory for public clients at
Google, and correct everywhere.

**`state`.** A random value echoed back and checked, so a redirect that did not
come from the request we made is refused instead of processed.

**127.0.0.1, not localhost, and not 0.0.0.0.** The literal address binds to the
loopback interface only. `localhost` can resolve to something else on a machine
with an edited hosts file, and 0.0.0.0 would accept the code from anyone on the
network.

**One request, then closed.** The listener exists for the seconds between
opening the browser and the answer coming back, and a timeout ends it if nobody
ever decides. A socket left open on a person's machine because they closed the
tab is not acceptable.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from typing import Any

from aven.harness.vault import Vault

# Long enough to find the browser window, read a consent screen and decide.
# Shorter than the patience of somebody who has walked away from the machine.
WAIT = 180

# Refreshed this long before it actually expires, so a call that takes a moment
# to reach the provider does not arrive holding a token that died in transit.
EARLY = 60


class Denied(Exception):
    """The person said no, or the provider refused."""


@dataclass
class Endpoints:
    """Where a provider's two OAuth URLs are, and what to ask it for."""

    authorize: str
    token: str
    scopes: list[str]
    # Google needs these to hand back a refresh token at all; most providers do
    # it unasked. Kept as plain extra parameters rather than a Google flag,
    # because the next provider will want two different ones.
    extra: dict[str, str] = field(default_factory=dict)


class OAuth:
    """An `Auth` for one service, keeping its tokens in the vault.

    Implements the protocol in `harness/connect.py`: `ready`, `sign_in`,
    `forget`, `about`. Everything else here is in service of `token()`, which is
    what the tools of that service actually call.
    """

    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        client_secret: str = "",
        endpoints: Endpoints,
        vault: Vault,
        open_browser=webbrowser.open,
    ) -> None:
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.endpoints = endpoints
        self.vault = vault
        # Injected so a test can drive the whole flow without a browser opening
        # on somebody's screen.
        self.open_browser = open_browser

    # -- the protocol --------------------------------------------------------

    def ready(self) -> bool:
        """Whether a call could be made right now without asking anybody.

        A live access token, or a refresh token that can fetch one. An expired
        access token with a refresh beside it is still ready: refreshing is
        this module's job and does not involve the person.
        """
        held = self.vault.get(self.name)
        if not held:
            return False
        return bool(held.get("refresh_token")) or not self._stale(held)

    def forget(self) -> bool:
        return self.vault.forget(self.name)

    def about(self) -> str:
        return self.vault.about()

    def sign_in(self) -> None:
        """The whole loopback dance. Blocks until it is decided or times out."""
        verifier = _random(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = _random(24)

        # Port 0 means "any free one". Bound before the URL is built, because the
        # redirect has to name the port that was actually granted.
        listener = _Catcher(("127.0.0.1", 0), _Handler)
        listener.timeout = WAIT
        redirect = f"http://127.0.0.1:{listener.server_port}/"

        asking = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(self.endpoints.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            **self.endpoints.extra,
        })

        try:
            self.open_browser(f"{self.endpoints.authorize}?{asking}")
            # One request and no more. A second one cannot be part of this flow,
            # and leaving the door open for it would be a listener on the
            # person's machine with no reason to exist.
            waiting = threading.Thread(target=listener.handle_request, daemon=True)
            waiting.start()
            waiting.join(WAIT + 5)
            answer = listener.answer
        finally:
            listener.server_close()

        if answer is None:
            raise Denied("nobody answered the sign-in page in time")
        if "error" in answer:
            raise Denied(answer.get("error_description") or answer["error"])
        if answer.get("state") != state:
            # Not a retryable failure. Something other than the page we opened
            # sent this, so the code in it is not ours to spend.
            raise Denied("the sign-in reply did not match the request")
        if "code" not in answer:
            raise Denied("the sign-in reply carried no code")

        got = self._ask_for_token({
            "grant_type": "authorization_code",
            "code": answer["code"],
            "redirect_uri": redirect,
            "code_verifier": verifier,
        })
        if not got.get("refresh_token"):
            # Without one, this works today and silently stops working in an
            # hour. Better to say so now than to look broken later.
            raise Denied(
                "the provider returned no refresh token, so this would stop "
                "working within the hour - check the consent screen was for "
                "offline access"
            )
        self._keep(got)

    # -- what the tools use --------------------------------------------------

    def token(self) -> str:
        """A usable access token, refreshed if it has gone stale.

        Raises rather than returning an empty string, because every caller is
        about to put this in an Authorization header and an empty one produces a
        401 that reads like the service is broken.
        """
        held = self.vault.get(self.name)
        if not held:
            raise Denied(f"{self.name} is not connected")
        if not self._stale(held):
            return str(held["access_token"])

        refresh = held.get("refresh_token")
        if not refresh:
            raise Denied(f"{self.name} needs connecting again")

        got = self._ask_for_token({
            "grant_type": "refresh_token",
            "refresh_token": str(refresh),
        })
        # A refresh usually answers without a new refresh token; the old one
        # stays valid and must not be overwritten with nothing.
        got.setdefault("refresh_token", refresh)
        self._keep(got)
        return str(got["access_token"])

    # -- the parts that touch the provider -----------------------------------

    def _ask_for_token(self, asking: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode({
            "client_id": self.client_id,
            **({"client_secret": self.client_secret} if self.client_secret else {}),
            **asking,
        }).encode()

        request = urllib.request.Request(
            self.endpoints.token,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                got = json.loads(answer.read().decode())
        except urllib.error.HTTPError as refused:
            detail = refused.read().decode(errors="replace")[:300]
            raise Denied(f"the provider refused: {detail}") from refused
        except (urllib.error.URLError, TimeoutError) as unreachable:
            raise Denied(f"could not reach the provider: {unreachable}") from unreachable

        if "access_token" not in got:
            raise Denied(f"the provider sent no token: {got}")
        return got

    def _keep(self, got: dict[str, Any]) -> None:
        """Store it with an absolute expiry.

        `expires_in` is a duration and is only meaningful next to the moment it
        was issued, which is not something a stored record has. Turned into a
        deadline here, once, rather than at every read.
        """
        self.vault.put(self.name, {
            "access_token": got["access_token"],
            "refresh_token": got.get("refresh_token", ""),
            "expires_at": time.time() + float(got.get("expires_in", 3600)),
            "scope": got.get("scope", " ".join(self.endpoints.scopes)),
        })

    @staticmethod
    def _stale(held: dict[str, Any]) -> bool:
        if not held.get("access_token"):
            return True
        return time.time() + EARLY >= float(held.get("expires_at", 0))


def _random(length: int) -> str:
    return secrets.token_urlsafe(length)[:length]


class _Catcher(http.server.HTTPServer):
    """The one-shot listener, holding whatever came back."""

    answer: dict[str, str] | None = None


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the name is the framework's
        asked = urllib.parse.urlparse(self.path)
        self.server.answer = {  # type: ignore[attr-defined]
            key: value[0]
            for key, value in urllib.parse.parse_qs(asked.query).items()
        }

        good = "code" in self.server.answer  # type: ignore[attr-defined]
        self.send_response(200 if good else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        # The last thing the person sees of this, so it says which program it
        # was and that they can leave. No styling, no fetch, nothing that would
        # make this page worth pointing a browser at twice.
        self.wfile.write(
            b"<!doctype html><meta charset=utf-8>"
            b"<body style='font:16px -apple-system,sans-serif;padding:3rem'>"
            + (
                b"<h2>Connected.</h2><p>You can close this tab and go back to aven."
                if good
                else b"<h2>Not connected.</h2><p>Go back to aven and try again."
            )
        )

    def log_message(self, *_: object) -> None:
        """Quiet. In `--mode rpc` stdout is the protocol, and the default here
        writes to stderr on every request for no reader's benefit."""
