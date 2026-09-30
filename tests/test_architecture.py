"""The layering, enforced.

Folders alone do not isolate anything - one convenient import and the harness
knows about the terminal again, and nobody notices until a second app needs it.
These tests are the isolation. They read the imports out of the source rather
than trusting a convention.

The rule is one-directional: an app may reach down to anything, the foundation
may reach down to the harness, and the harness may reach nowhere.

    apps/cli_assistant            apps/cli_code
            |                            |
            +----> terminal, toolkit <---+
                              |
                           harness
                              |
                            model
"""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "aven"

# What each layer is allowed to import from, beyond the standard library and
# third-party packages. `harness` is deliberately not in its own list: nothing
# above it may be reached, and nothing beside it either.
# `text` sits below the harness rather than beside it: every layer has something
# to tell somebody, so every layer may reach the catalogue, and the catalogue
# reaches nothing.
#
# `wire` sits below `terminal` rather than beside it. Conceptually they are
# siblings - one interface for a person, one for a program - but the dependency
# points one way: the CLI is what launches --mode rpc, and the terminal's JSON
# sink reuses the wire shapes so there is one definition of what aven looks like
# from outside. This table is about dependencies, not about concepts.
ALLOWED = {
    "text": {"text"},
    "harness": {"harness", "text"},
    "model": {"harness", "model", "text"},
    "toolkit": {"harness", "model", "toolkit", "text"},
    "wire": {"harness", "model", "toolkit", "wire", "text"},
    "terminal": {"harness", "model", "toolkit", "wire", "terminal", "text"},
    "apps": {"harness", "model", "toolkit", "wire", "terminal", "apps", "text"},
}

CJK = re.compile(r"[一-鿿]")


def layer(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).parts[0]


def modules() -> list[pathlib.Path]:
    return [p for p in sorted(ROOT.rglob("*.py")) if p.name != "__init__.py" or p.parent != ROOT]


def imports(path: pathlib.Path) -> set[str]:
    """Every `aven.<layer>` this module imports, at any depth, including inside
    functions - a deferred import is still a dependency."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            name = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("aven."):
                    found.add(alias.name.split(".")[1])
            continue
        else:
            continue
        if name.startswith("aven."):
            found.add(name.split(".")[1])

    return found


def test_every_module_sits_in_a_layer_that_is_declared():
    """A new top-level folder has to say what it may depend on."""
    assert {layer(p) for p in modules()} <= set(ALLOWED) | {"__init__.py"}


def test_nothing_imports_upwards():
    broken = []
    for path in modules():
        here = layer(path)
        if here not in ALLOWED:
            continue
        for wanted in imports(path):
            if wanted not in ALLOWED[here]:
                broken.append(f"{path.relative_to(ROOT)} imports aven.{wanted}")

    assert broken == [], "\n".join(broken)


def test_the_harness_knows_nothing_about_any_app():
    """The point of the whole arrangement, stated on its own.

    If this fails, some part of being an agent has been written in terms of one
    particular thing an agent is for.
    """
    reached = {w for p in modules() if layer(p) == "harness" for w in imports(p)}

    assert reached <= {"harness", "text"}


def test_the_apps_do_not_reach_into_each_other():
    """They share through the layers below, or they do not share."""
    for path in modules():
        if layer(path) != "apps":
            continue
        mine = path.relative_to(ROOT).parts[1]
        source = path.read_text(encoding="utf-8")
        others = [
            other.name
            for other in (ROOT / "apps").iterdir()
            if other.is_dir() and other.name != mine and not other.name.startswith("_")
        ]
        for other in others:
            assert f"aven.apps.{other}" not in source, f"{path.name} reaches into {other}"


def test_no_text_for_a_person_below_the_apps():
    """The harness and the foundation must not assume the reader's language.

    Anything with a CJK character in it is text somebody reads, and text
    somebody reads belongs to an app - or to the catalogue an app chooses from.
    An English string is not proof of the opposite, so this catches one
    direction only; it is the direction that actually goes wrong.
    """
    offenders = []
    for path in modules():
        if layer(path) not in ("harness", "model", "toolkit", "terminal"):
            continue
        if path.parts[-2:] == ("text", "zh.py"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CJK.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}  {line.strip()[:60]}")

    assert offenders == [], "\n".join(offenders[:40])
