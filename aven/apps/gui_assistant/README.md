# gui_assistant

The desktop window lives at [`gui/`](../../../gui) in the repository root, not
here. It is a Tauri app — React, TypeScript and a Rust shell — so it is not a
Python package and has no business inside one.

There is no Python code in this folder and there may never need to be. The window
does not import aven; it spawns `aven --mode rpc` as a sidecar and speaks the
protocol in `aven/wire/`. That is the whole coupling, and it is the same coupling
a phone client will have.

If a GUI-specific app ever does need a Python side — its own system prompt, its
own session directory, its own tool selection — it goes here as a `Blueprint`,
exactly like `cli_assistant` and `cli_code`. Until then this folder is a signpost.

## The constraint that matters

If building the window needs a change below `apps/`, that change is a bug in the
layering rather than a requirement of the window — and `tests/test_architecture.py`
will say so. So far it has needed none: multiple roots, structured previews,
standing approvals, triggers and the RPC protocol were all built in the harness
and are all used by the terminal too.
