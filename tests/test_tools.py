"""Tests for the tool protocol."""

from typing import Annotated

import pytest

from aven.core.tools import Tool, ToolResult, schema_of, tool


def test_schema_comes_from_the_signature():
    @tool()
    def move_file(src: str, dst: str, overwrite: bool = False) -> str:
        """Move a file to another folder."""
        return "ok"

    assert move_file.name == "move_file"
    assert move_file.description == "Move a file to another folder."
    assert move_file.schema == {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "dst": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["src", "dst"],
    }


def test_defaulted_parameters_are_not_required():
    @tool()
    def f(a: str, b: int = 1) -> str:
        return "ok"

    assert f.schema["required"] == ["a"]


def test_annotated_descriptions_reach_the_model():
    @tool()
    def f(path: Annotated[str, "Absolute path to read"]) -> str:
        return "ok"

    assert f.schema["properties"]["path"]["description"] == "Absolute path to read"


def test_string_annotations_are_resolved_not_compared_as_text():
    """This module uses `from __future__ import annotations` everywhere.

    Reading __annotations__ directly would give "int", never the int type, and
    every parameter would silently fall back to "string".
    """
    assert schema_of(lambda n: n) is not None

    @tool()
    def f(n: int, ratio: float, flag: bool) -> str:
        return "ok"

    types = {k: v["type"] for k, v in f.schema["properties"].items()}
    assert types == {"n": "integer", "ratio": "number", "flag": "boolean"}


def test_risk_defaults_to_read():
    @tool()
    def f() -> str:
        return "ok"

    assert f.risk == "read"


def test_preview_renders_before_anything_runs():
    ran = []

    @tool(risk="reversible", preview="{src} → {dst}")
    def move(src: str, dst: str) -> str:
        ran.append(1)
        return "moved"

    assert move.preview({"src": "a.pdf", "dst": "b/a.pdf"}) == "a.pdf → b/a.pdf"
    assert ran == [], "preview must not execute the tool"


def test_preview_falls_back_to_the_call_itself():
    @tool()
    def f(a: str) -> str:
        return "ok"

    assert f.preview({"a": "x"}) == "f(a='x')"


def test_a_plain_return_is_wrapped_with_no_undo():
    @tool()
    def f() -> str:
        return 42

    result = f()
    assert isinstance(result, ToolResult)
    assert result.output == "42"
    assert result.undo is None


def test_undo_closes_over_what_the_tool_actually_did():
    box = {"here": True}

    @tool(risk="reversible")
    def take() -> ToolResult:
        box["here"] = False
        return ToolResult(output="taken", undo=lambda: box.__setitem__("here", True))

    result = take()
    assert box["here"] is False
    result.undo()
    assert box["here"] is True


def test_for_model_is_the_anthropic_shape():
    @tool(description="Do a thing")
    def f(a: str) -> str:
        return "ok"

    assert set(f.for_model()) == {"name", "description", "input_schema"}
    assert f.for_model()["input_schema"] == f.schema


def test_a_decorated_tool_is_a_Tool_not_a_function():
    @tool()
    def f() -> str:
        return "ok"

    assert isinstance(f, Tool)
    assert f().output == "ok"
