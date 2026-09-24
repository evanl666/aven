"""Guards on pyproject itself.

A key written under the wrong table still parses, so a mistake here is silent:
`dependencies` once sat inside [project.urls], which meant an install of aven
pulled in neither anthropic nor textual. Nothing failed locally, because the
development venv already had both.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def project() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_runtime_dependencies_are_declared_on_the_project():
    declared = project().get("dependencies", [])
    assert any(d.startswith("anthropic") for d in declared)
    assert any(d.startswith("textual") for d in declared)


def test_urls_holds_urls_and_nothing_else():
    assert set(project()["urls"]) == {"Homepage", "Issues"}


def test_the_command_points_at_something_importable():
    module, _, attribute = project()["scripts"]["aven"].partition(":")
    import importlib

    assert callable(getattr(importlib.import_module(module), attribute))
