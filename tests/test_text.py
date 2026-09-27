"""Tests for the text catalogue.

Two kinds of thing here. The mechanism - how a language is chosen and what
happens when a string is missing - and one test that reads the whole source for
keys and checks every one of them exists. The second is the one that earns its
place: a mistyped key is invisible until somebody reaches that line of the
interface.
"""

import ast
import pathlib

import pytest

from aven import text
from aven.text import en, zh

ROOT = pathlib.Path(__file__).resolve().parent.parent / "aven"


# --- the mechanism -----------------------------------------------------------


def test_a_string_comes_back_with_its_parameters_filled_in():
    text.use("en")
    assert text.t("tray.summary", pending=2, applied=1) == (
        "2 waiting for approval / 1 done, undoable"
    )


def test_a_key_nobody_wrote_comes_back_as_itself():
    """A blemish on screen beats a traceback from the renderer mid-run, and the
    key is exactly the thing to go and add."""
    assert text.t("nothing.like.this") == "nothing.like.this"


def test_a_key_missing_from_a_catalogue_falls_back_to_english():
    text.use("zh")
    assert "zh" not in str(zh.TEXT.get("tray.empty"))  # it has its own
    assert text.t("cli.needs_prompt").startswith("-p"), "both catalogues have this"

    # Something only English has.
    en.TEXT["testing.only.english"] = "only here"
    try:
        assert text.t("testing.only.english") == "only here"
    finally:
        del en.TEXT["testing.only.english"]


def test_a_template_written_against_an_older_call_site_still_says_something():
    en.TEXT["testing.stale"] = "expected {gone}"
    try:
        text.use("en")
        assert text.t("testing.stale", different=1) == "expected {gone}"
    finally:
        del en.TEXT["testing.stale"]


# --- choosing the language ---------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("zh", "zh"),
        ("ZH", "zh"),
        ("zh_CN.UTF-8", "zh"),
        ("zh-Hans", "zh"),
        ("en_GB.UTF-8", "en"),
        ("fr_FR.UTF-8", "en"),
        ("garbage", "en"),
    ],
)
def test_a_locale_is_read_loosely(value, expected, monkeypatch):
    monkeypatch.setenv("AVEN_LANG", value)
    text.use(None)
    assert text.language() == expected


def test_aven_lang_wins_over_the_locale(monkeypatch):
    """One was set on purpose; the other came with the machine."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("AVEN_LANG", "zh")
    text.use(None)

    assert text.language() == "zh"


def test_the_locale_is_used_when_nothing_was_asked_for(monkeypatch):
    monkeypatch.delenv("AVEN_LANG", raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    text.use(None)

    assert text.language() == "zh"


def test_english_when_the_machine_says_nothing(monkeypatch):
    for name in ("AVEN_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    text.use(None)

    assert text.language() == "en"


def test_the_language_is_worked_out_once(monkeypatch):
    """A language that changes halfway through leaves half a screen behind."""
    monkeypatch.setenv("AVEN_LANG", "zh")
    text.use(None)
    assert text.language() == "zh"

    monkeypatch.setenv("AVEN_LANG", "en")
    assert text.language() == "zh", "still, until something clears the choice"


def test_switching_actually_changes_what_aven_says():
    text.use("en")
    english = text.t("tray.empty")
    text.use("zh")
    chinese = text.t("tray.empty")

    assert english != chinese
    assert english == "nothing changed"
    assert chinese == "没有任何改动"


# --- the catalogue against the source ----------------------------------------


def keys_used() -> dict[str, list[str]]:
    """Every key passed to `t()` anywhere in aven, and where from.

    Read out of the syntax tree, so `t(key)` with a variable is skipped rather
    than guessed at - there are none today, and a guess would be worse than a
    gap.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "t" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, []).append(
                    f"{path.relative_to(ROOT)}:{node.lineno}"
                )
    return found


def test_every_key_the_code_asks_for_exists_in_english():
    """A mistyped key is invisible until somebody reaches that line."""
    missing = [
        f"{key}  ({', '.join(where)})"
        for key, where in sorted(keys_used().items())
        if key not in en.TEXT
    ]

    assert missing == [], "\n".join(missing)


def test_the_keys_are_asked_for_somewhere():
    """A catalogue only grows otherwise, and nobody can tell what is dead.

    Keys built by interpolation - `tray.state.{state}`, `ago.{unit}` - are
    listed here by their prefix, because no literal for them appears anywhere.
    """
    built = ("tray.state.", "ago.", "tree.who.", "memory.where.", "shell.describe.")
    asked = set(keys_used())

    unused = [
        key
        for key in sorted(en.TEXT)
        if key not in asked and not key.startswith(built)
    ]

    assert unused == [], "\n".join(unused)


def test_the_chinese_catalogue_invents_no_keys_of_its_own():
    """A key only zh has is unreachable: `t` reads en to know it exists."""
    assert sorted(set(zh.TEXT) - set(en.TEXT)) == []
