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
