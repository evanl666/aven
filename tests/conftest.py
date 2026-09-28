"""Fixtures every test gets.

The interface language is pinned to English, on purpose. Without this the suite
reads whatever locale the machine running it happens to have, and an assertion
on what aven says passes here and fails in CI. A test that cares about another
language asks for it itself.
"""

import pytest

from aven import text


@pytest.fixture(autouse=True)
def english():
    text.use("en")
    yield
    text.use(None)


@pytest.fixture(autouse=True)
def nobodys_keychain(tmp_path_factory, monkeypatch):
    """No test ever touches the real one.

    Two different accidents, and the second is the serious one. A test that
    *reads* it passes or fails depending on whose machine it runs on - "no key
    anywhere" is not a state you can reach on a laptop that has one. A test
    that *writes* would put something in the developer's login keychain, which
    is not a thing a test suite is entitled to do.

    Pointed at a temporary file rather than disabled, so the code under test
    takes the same path it takes in production.
    """
    from aven.harness import vault as real
    from aven.terminal import app

    kept = real.Locked(tmp_path_factory.mktemp("vault") / "credentials.json")
    monkeypatch.setattr(real, "vault_for", lambda *a, **k: kept)
    monkeypatch.setattr(app, "vault_for", lambda *a, **k: kept)
    yield kept
