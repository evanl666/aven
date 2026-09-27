"""The tool protocol.

A tool is not a bare function. Before the model may call it, aven needs to know
four things, and all four are declared at the definition site:

  how to call it    the JSON schema, derived from the signature so it cannot
                    drift away from the code
  how risky it is   read / reversible / irreversible - Step 5 gates on this
  what it would do  a preview rendered from the arguments, before anything runs -
                    one line always, and optionally something a window can draw:
                    a diff, the body of a draft, a list of moves, an order
  how to take it back  an undo returned alongside the result

The last two are why `aven` exists. A coding agent can be forgiven for acting
first; an assistant moving a person's files and sending their mail cannot.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

@dataclass(frozen=True, kw_only=True)
class Diff:
    """One passage replaced by another, so the change can be read rather than
    described. What `edit_file` and `remember` actually do."""

    before: str
    after: str
    path: str = ""


@dataclass(frozen=True, kw_only=True)
class Body:
    """A block of text a person should read before it goes out - the mail that
    would be sent, the message that would be posted."""

    text: str
    title: str = ""


@dataclass(frozen=True, kw_only=True)
class Moves:
    """Where things would end up, as pairs. One move fits on a line; forty do
    not, and forty is where reading the line stops being enough."""

    pairs: list[tuple[str, str]]


@dataclass(frozen=True, kw_only=True)
class Order:
    """What would be bought, and for how much.

    Money is the case a single line is least adequate for. "order 3 items from
    Amazon" is not something a person can approve; a list with prices and a
    total is. Anything that spends belongs here.
    """

    items: list[tuple[str, str]]  # description, price as written
    total: str
    where: str = ""
    account: str = ""
    arrives: str = ""


# What a call would do, in a shape something richer than a terminal line can
# draw. Always optional: every surface must work from `preview` alone, because
# most tools have nothing more to say and a renderer cannot require it.
Detail = Diff | Body | Moves | Order


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
    detail_with: Callable[..., Detail | None] | None = None

    # Whether a standing approval may ever cover this tool. See
    # harness/tx/standing.py: some things must be decided one at a time, every
    # time, and anything that spends money is one of them.
    #
    # No default, like risk: a field that decides whether a person can be asked
    # once instead of every time is one the author has to state. `tool()` gives
    # it one, which is where the default belongs - somewhere a test can reach.
    pre_approvable: bool

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

    def detail_for(self, args: dict[str, Any]) -> Detail | None:
        """The richer preview, if this tool has one and can build it.

        Never raises. A detail is a courtesy to whoever is drawing the approval
        surface, and a courtesy that can break the run is not one - a file that
        has since been deleted must not stop the call being described.
        """
        if self.detail_with is None:
            return None
        try:
            return self.detail_with(**args)
        except Exception:
            return None

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


def as_dict(detail: Detail | None) -> dict[str, Any] | None:
    """A detail as plain json, tagged with which shape it is.

    Written by hand for the same reason the event stream is: this crosses a
    process boundary, so the names are a decision rather than whatever the
    dataclasses happen to be called today.
    """
    match detail:
        case None:
            return None
        case Diff():
            return {"kind": "diff", "path": detail.path,
                    "before": detail.before, "after": detail.after}
        case Body():
            return {"kind": "body", "title": detail.title, "text": detail.text}
        case Moves():
            return {"kind": "moves", "pairs": [list(p) for p in detail.pairs]}
        case Order():
            return {"kind": "order", "items": [list(i) for i in detail.items],
                    "total": detail.total, "where": detail.where,
                    "account": detail.account, "arrives": detail.arrives}
    raise TypeError(f"no json form for {type(detail).__name__}")


def tool(
    *,
    risk: Risk | RiskFn = "read",
    preview: str | Callable[..., str] | None = None,
    detail: Callable[..., Detail | None] | None = None,
    pre_approvable: bool = True,
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
            detail_with=detail,
            pre_approvable=pre_approvable,
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
