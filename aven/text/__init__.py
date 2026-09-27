"""What aven says, kept apart from the code that decides to say it.

The bottom layer, below the harness: every layer may reach it, because every
layer has something to tell somebody. A module here names what to say and the
catalogue says it, which is what lets the harness carry no sentence in any
particular language.

English is the source of truth. A key missing from another catalogue falls back
to it rather than failing, so adding a string is one edit and translating it is
a separate, optional one - and a half-translated catalogue degrades to English
instead of showing a raw key.

Only text a person reads belongs here. What the model reads is English in the
source, always: instructions to a model are code, they are read next to the
logic they steer, and a model answers in the language it was addressed in
whatever language it was instructed in.
"""

from __future__ import annotations

import os
from typing import Any

from aven.text import en, zh

CATALOGUES: dict[str, dict[str, str]] = {"en": en.TEXT, "zh": zh.TEXT}

FALLBACK = "en"

_chosen: str | None = None


def language() -> str:
    """Which catalogue to read, worked out once and remembered.

    AVEN_LANG wins because it is the one a person set on purpose. Otherwise the
    locale, which is usually right and never worth asking about.
    """
    global _chosen
    if _chosen is None:
        _chosen = _from_environment()
    return _chosen


def use(name: str | None) -> None:
    """Force a language, or clear the choice so it is worked out again.

    Exists for tests and for a `--lang` flag. Nothing else should call it: a
    language that changes halfway through a run leaves half a screen behind.
    """
    global _chosen
    _chosen = name if name is None else _normalise(name)


def t(key: str, /, **params: Any) -> str:
    """The string for `key`, with `params` substituted.

    An unknown key comes back as itself rather than raising. A missing string is
    a blemish; a traceback from the renderer in the middle of a run is worse,
    and the key on screen says exactly what to go and add.
    """
    catalogue = CATALOGUES.get(language(), en.TEXT)
    template = catalogue.get(key) or en.TEXT.get(key)
    if template is None:
        return key
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        # A catalogue written against an older call site. Say something rather
        # than nothing, and let the key point at what needs fixing.
        return template


def _from_environment() -> str:
    asked = os.environ.get("AVEN_LANG")
    if asked:
        return _normalise(asked)

    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            return _normalise(value)

    return FALLBACK


def _normalise(value: str) -> str:
    """"zh_CN.UTF-8", "zh-Hans", "ZH" all mean the same catalogue."""
    tag = value.replace("_", "-").split(".")[0].lower()
    if tag in CATALOGUES:
        return tag
    base = tag.split("-")[0]
    return base if base in CATALOGUES else FALLBACK


__all__ = ["language", "t", "use"]
