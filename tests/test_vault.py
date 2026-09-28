"""Where credentials go, and who else can read them."""

import json
import os
import stat

from aven.harness.vault import Locked, Nowhere, vault_for


def test_a_secret_survives_a_round_trip(tmp_path):
    vault = Locked(tmp_path / "creds.json")
    vault.put("google", {"access_token": "abc", "expires_at": 123.5})

    assert vault.get("google") == {"access_token": "abc", "expires_at": 123.5}


def test_the_file_is_never_readable_by_anybody_else(tmp_path):
    """A file that spends even a moment at the default 644 with a refresh token
    in it has already been readable by every process on the machine."""
    path = tmp_path / "creds.json"
    Locked(path).put("google", {"access_token": "abc"})

    mode = stat.S_IMODE(os.stat(path).st_mode)

    assert mode == 0o600, f"others can read this: {oct(mode)}"


def test_a_file_that_already_existed_wide_open_is_narrowed(tmp_path):
    """The interesting case is an upgrade, not a fresh install: a file written
    by an older version, or restored from a backup that did not keep modes."""
    path = tmp_path / "creds.json"
    path.write_text("{}")
    os.chmod(path, 0o644)

    Locked(path).put("google", {"access_token": "abc"})

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_two_connectors_do_not_overwrite_each_other(tmp_path):
    vault = Locked(tmp_path / "creds.json")
    vault.put("google", {"access_token": "g"})
    vault.put("dropbox", {"access_token": "d"})

    assert vault.get("google") == {"access_token": "g"}
    assert vault.get("dropbox") == {"access_token": "d"}


def test_forgetting_one_leaves_the_others(tmp_path):
    vault = Locked(tmp_path / "creds.json")
    vault.put("google", {"access_token": "g"})
    vault.put("dropbox", {"access_token": "d"})

    assert vault.forget("google") is True
    assert vault.forget("google") is False, "nothing to forget the second time"
    assert vault.get("google") is None
    assert vault.get("dropbox") == {"access_token": "d"}


def test_a_corrupted_file_reads_as_empty_rather_than_raising(tmp_path):
    """Treating it as absent is what lets signing in again fix it. Raising would
    leave every connector permanently stuck behind one bad byte."""
    path = tmp_path / "creds.json"
    path.write_text("this is not json{{{")

    vault = Locked(path)

    assert vault.get("google") is None
    vault.put("google", {"access_token": "recovered"})
    assert vault.get("google") == {"access_token": "recovered"}


def test_a_missing_file_is_not_an_error(tmp_path):
    assert Locked(tmp_path / "nothing" / "here.json").get("google") is None


def test_the_memory_vault_keeps_nothing_past_itself():
    vault = Nowhere()
    vault.put("google", {"access_token": "abc"})

    assert vault.get("google") == {"access_token": "abc"}
    assert Nowhere().get("google") is None, "a new one starts empty"


def test_every_vault_says_where_it_puts_things(tmp_path):
    """A person deciding whether to sign a service in is entitled to know
    whether the token lands in a keychain or in their home directory."""
    for vault in (Locked(tmp_path / "c.json"), Nowhere(), vault_for(tmp_path)):
        assert vault.about().strip(), f"{type(vault).__name__} says nothing"


def test_the_fallback_names_the_file_it_actually_uses(tmp_path):
    vault = Locked(tmp_path / "creds.json")

    assert str(tmp_path / "creds.json") in vault.about()
    assert "keychain" in vault.about(), "and that this is the weaker option"


def test_what_lands_on_disk_is_only_what_was_put_there(tmp_path):
    """No stray copy under another key, and nothing added helpfully."""
    path = tmp_path / "creds.json"
    Locked(path).put("google", {"access_token": "abc"})

    assert json.loads(path.read_text()) == {"google": {"access_token": "abc"}}
