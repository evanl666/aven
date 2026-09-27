"""The tool protocol.

A tool is not a bare function. Before the model may call it, aven needs to know
four things, and all four are declared at the definition site:

  how to call it    the JSON schema, derived from the signature so it cannot
                    drift away from the code
  how risky it is   read / reversible / irreversible - Step 5 gates on this
  what it would do  a preview rendered from the arguments, before anything runs
  how to take it back  an undo returned alongside the result

The last two are why `aven` exists. A coding agent can be forgiven for acting
first; an assistant moving a person's files and sending their mail cannot.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

# read          no side effect at all - never needs approval
# reversible    changes the world, but undo() puts it back
# irreversible  sent, paid, deleted - Step 5 will require an explicit commit
Risk = Literal["read", "reversible", "irreversible"]

# A tool may work its risk out from its own arguments instead of declaring one
# constant. See Tool.risk_for for when that is the honest thing to do.
RiskFn = Callable[..., Risk]

_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass(frozen=True, kw_only=True)
class ToolResult:
    """What a tool hands back.

    `undo` is a closure over whatever the tool needs to reverse itself - the
    destination path it actually used, the id of the row it inserted. Only the
    tool knows that, so only the tool can build it.
    """

    output: str
    undo: Callable[[], None] | None = None


@dataclass(frozen=True, kw_only=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    risk: Risk | RiskFn
    fn: Callable[..., ToolResult | object]
    preview_with: str | Callable[..., str] | None = None

    def risk_for(self, args: dict[str, Any]) -> Risk:
        """How risky this particular call is.

        Nearly every tool is one risk always: write_file writes, list_dir reads.
        A tool that runs whatever it is handed has no such answer. Declaring a
        shell irreversible makes `ls` wait for a keypress; declaring it read is a
        lie the tray has no way to catch.

        So a tool may decide from its own arguments. This is not the policy
        layer and does not weaken it: policy is the person's overlay and may
        still only raise what comes out of here. A tool lowering its own risk is
        a tool describing itself, which is the one place that judgement belongs.
        """
        if callable(self.risk):
            return self.risk(**args)
        return self.risk

    def preview(self, args: dict[str, Any]) -> str:
        """What this call would do, in the user's words, before it runs."""
        if self.preview_with is None:
            rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
            return f"{self.name}({rendered})"
        if callable(self.preview_with):
            return self.preview_with(**args)
        return self.preview_with.format(**args)

    def __call__(self, **args: Any) -> ToolResult:
        """Run it. A tool may return a bare value when it has nothing to undo."""
        out = self.fn(**args)
        return out if isinstance(out, ToolResult) else ToolResult(output=str(out))

    def for_model(self) -> dict[str, Any]:
        """The shape Anthropic expects in the `tools` array."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }


def tool(
    *,
    risk: Risk | RiskFn = "read",
    preview: str | Callable[..., str] | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Turn a function into a Tool.

    Defaults to `read` on purpose: a tool that changes the world has to say so
    out loud, so nothing slips past Step 5's gate by omission.
    """

    def wrap(fn: Callable[..., Any]) -> Tool:
        return Tool(
            name=name or fn.__name__,
            description=description or (inspect.getdoc(fn) or "").strip(),
            schema=schema_of(fn),
            risk=risk,
            fn=fn,
            preview_with=preview,
        )

    return wrap


def schema_of(fn: Callable[..., Any]) -> dict[str, Any]:
    """Derive a JSON schema from a function's signature.

    `get_type_hints` rather than reading `__annotations__`: this project uses
    `from __future__ import annotations`, so the raw annotations are strings
    like "str" and comparing them to `str` would silently never match.
    `include_extras` keeps Annotated alive so a parameter can carry its own
    description - the model gets much less creative when each argument says
    what it is for.
    """
    hints = get_type_hints(fn, include_extras=True)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in inspect.signature(fn).parameters.items():
        hint = hints.get(param_name, str)

        description = None
        if get_origin(hint) is Annotated:
            hint, *extras = get_args(hint)
            description = next((e for e in extras if isinstance(e, str)), None)

        prop: dict[str, Any] = {"type": _JSON_TYPES.get(hint, "string")}
        if description:
            prop["description"] = description
        properties[param_name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {"type": "object", "properties": properties, "required": required}
