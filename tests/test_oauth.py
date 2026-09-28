"""The loopback sign-in, driven end to end against a provider we control.

Not mocked at the seam that matters. A real socket is bound, a real redirect is
made to it, and a real token request goes out over HTTP - because the parts most
likely to be wrong are the ones a mock would replace: whether the redirect URI
names the port actually granted, whether `state` is really checked, whether the
verifier sent at the end matches the challenge sent at the start.
"""

import base64
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aven.harness.vault import Nowhere
from aven.toolkit.oauth import Denied, Endpoints, OAuth


class Provider:
    """A stand-in for Google: issues tokens, and checks PKCE the way it should."""

    def __init__(self) -> None:
        self.asked: list[dict[str, str]] = []
        self.refresh_count = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                form = {
                    k: v[0]
                    for k, v in urllib.parse.parse_qs(
                        self.rfile.read(length).decode()
                    ).items()
                }
                provider.asked.append(form)

                if form.get("grant_type") == "refresh_token":
                    provider.refresh_count += 1
                    body = {"access_token": "second", "expires_in": 3600}
                else:
                    # The whole point of PKCE: the code is worthless without the
                    # verifier, and the verifier must hash to the challenge.
                    want = provider.challenge_for.get(form.get("code", ""))
                    got = (
                        base64.urlsafe_b64encode(
                            hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                        )
                        .decode()
                        .rstrip("=")
                    )
                    if want != got:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(b'{"error":"invalid_grant"}')
                        return
                    body = {"access_token": "first", "expires_in": 3600}
                    if not provider.stingy:
                        body["refresh_token"] = "lasting"

                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_: object) -> None:
                pass

        self.challenge_for: dict[str, str] = {}
        # When true, the provider answers without a refresh token - which is
        # what Google does if `access_type=offline` was left off.
        self.stingy = False
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def token_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/token"

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture
def provider():
    made = Provider()
    yield made
    made.stop()


def browser_that(answer, provider=None, code="the-code"):
    """A fake browser: reads the consent URL and sends a redirect back.

    On a thread, because the redirect has to arrive while `sign_in` is still
    waiting for it - a blocking call here would deadlock against the listener it
    is trying to reach.
    """

    def open_it(url: str) -> bool:
        asked = dict(
            (k, v[0]) for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()
        )
        if provider is not None:
            provider.challenge_for[code] = asked["code_challenge"]

        def knock() -> None:
            time.sleep(0.05)
            back = urllib.parse.urlencode(answer(asked))
            try:
                urllib.request.urlopen(f"{asked['redirect_uri']}?{back}", timeout=5).read()
            except Exception:
                pass

        threading.Thread(target=knock, daemon=True).start()
        return True

    return open_it


def build(provider, open_browser, vault=None) -> OAuth:
    return OAuth(
        name="test",
        client_id="a-client",
        endpoints=Endpoints(
            authorize="http://127.0.0.1:1/authorize",  # never actually fetched
            token=provider.token_url,
            scopes=["one", "two"],
        ),
        vault=vault or Nowhere(),
        open_browser=open_browser,
    )


def test_a_whole_sign_in_ends_with_a_token_in_the_vault(provider):
    vault = Nowhere()
    auth = build(
        provider,
        browser_that(lambda asked: {"code": "the-code", "state": asked["state"]}, provider),
        vault,
    )

    assert auth.ready() is False
    auth.sign_in()

    assert auth.ready() is True
    assert auth.token() == "first"
    assert vault.get("test")["refresh_token"] == "lasting"


def test_the_verifier_never_travels_with_the_request_that_starts_it(provider):
    """PKCE only works if the challenge goes out and the verifier stays behind.

    Sending both would be an elaborate way of sending neither: anything that
    read the first request would hold everything needed to spend the code.
    """
    seen: dict[str, str] = {}

    def watch(url: str) -> bool:
        seen.update(
            (k, v[0])
            for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()
        )
        provider.challenge_for["the-code"] = seen["code_challenge"]

        def knock() -> None:
            time.sleep(0.05)
            back = urllib.parse.urlencode({"code": "the-code", "state": seen["state"]})
            try:
                urllib.request.urlopen(f"{seen['redirect_uri']}?{back}", timeout=5).read()
            except Exception:
                pass

        threading.Thread(target=knock, daemon=True).start()
        return True

    build(provider, watch).sign_in()

    assert seen["code_challenge_method"] == "S256"
    assert "code_verifier" not in seen
    # And it does arrive later, with the exchange.
    assert "code_verifier" in provider.asked[0]


def test_a_redirect_with_the_wrong_state_is_refused(provider):
    """Anything can knock on a port on this machine. Only the page we opened
    knows the value we sent it, so a reply without it is not ours to act on."""
    auth = build(
        provider,
        browser_that(lambda asked: {"code": "the-code", "state": "somebody-else"}, provider),
    )

    with pytest.raises(Denied, match="did not match"):
        auth.sign_in()

    assert auth.ready() is False
    assert provider.asked == [], "and the code was never spent"


def test_the_person_saying_no_is_reported_as_such(provider):
    auth = build(
        provider,
        browser_that(lambda asked: {"error": "access_denied",
                                    "error_description": "you said no"}),
    )

    with pytest.raises(Denied, match="you said no"):
        auth.sign_in()


def test_a_provider_that_returns_no_refresh_token_is_refused_at_once(provider):
    """It would work for an hour and then stop, which is the worst kind of
    broken: it demos perfectly and fails after everybody has gone home.

    This is what Google does when `access_type=offline` is left off, so it is a
    configuration mistake somebody will actually make.
    """
    provider.stingy = True
    vault = Nowhere()
    auth = build(
        provider,
        browser_that(lambda asked: {"code": "the-code", "state": asked["state"]}, provider),
        vault,
    )

    with pytest.raises(Denied, match="refresh token"):
        auth.sign_in()

    assert vault.get("test") is None, (
        "half a sign-in must not be stored: it would look connected and fail later"
    )


def test_an_expired_token_is_refreshed_without_asking_anybody(provider):
    vault = Nowhere()
    auth = build(
        provider,
        browser_that(lambda asked: {"code": "the-code", "state": asked["state"]}, provider),
        vault,
    )
    auth.sign_in()

    # Age it past the early-refresh window.
    held = vault.get("test")
    held["expires_at"] = time.time() - 1
    vault.put("test", held)

    assert auth.ready() is True, "a refresh token means no person is needed"
    assert auth.token() == "second"
    assert provider.refresh_count == 1
    assert vault.get("test")["refresh_token"] == "lasting", (
        "a refresh answers without a new one; the old must not be overwritten"
    )


def test_forgetting_leaves_nothing_behind(provider):
    vault = Nowhere()
    auth = build(
        provider,
        browser_that(lambda asked: {"code": "the-code", "state": asked["state"]}, provider),
        vault,
    )
    auth.sign_in()

    assert auth.forget() is True
    assert auth.ready() is False
    assert vault.get("test") is None
    with pytest.raises(Denied, match="not connected"):
        auth.token()
