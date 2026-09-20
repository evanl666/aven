# aven

A local-first personal assistant harness. Your machine, your keys, your data.

Where a coding agent assumes a developer in a repo, `aven` assumes a person on
their own computer with real accounts, real money, and real irreversible
actions. That changes every design decision.

## Design commitments

These are decided up front because retrofitting any of them is painful.

1. **The model gets handles, not values.** Private data is replaced by opaque
   handles (`<<person:7>>`) before the prompt leaves the machine, and
   dereferenced only inside the tool executor. A hijacked model cannot leak what
   was never in its context.
2. **Side effects are staged, not fired.** Tools produce entries in a pending
   tray with `preview()` and `undo()`. The user approves a batch and sees a diff
   of their life; irreversible actions require an explicit commit.
3. **Repeated tasks crystallise into readable programs.** A task done twice
   becomes a routine the user can read and edit, executed deterministically with
   the model only at the ambiguous steps.
4. **Tiered actuation.** native API -> Shortcuts -> AppleScript/AX -> vision
   clicking, cheapest rung first, remembering which rung worked.
5. **Triggers first, not chat first.** A turn can be opened by cron, a new mail,
   or a file landing in `~/Downloads`. Chat is one trigger among many.
6. **Untrusted content never authorises an action.** Content read from mail, web
   pages, or documents enters tagged; tool calls tainted by it need policy
   approval or a human.

## Layout

```
aven/
  core/        messages, events, agent loop, tool protocol
  tx/          staging / commit / undo          <- the heart
  actuators/   native | shortcuts | osascript | browser
  daemon/      resident process + trigger bus
  cli/         a client of the daemon, nothing more
```

`aven` is a plain package. Third-party extensions will be discovered through
packaging entry points (the mechanism pytest and flake8 use), not by importing
into this tree.

## Status

| Step | | |
|---|---|---|
| 1 | Message types, session/LLM split | **done** |
| 2 | Session tree, JSONL store, branching | **done** |
| 3 | Event stream + agent loop | **done** |
| 4 | Tool protocol (`preview` / `undo` / `reversible`) | **done** |
| 5 | Transaction layer: staging, commit, undo | next |
| 6 | Real model, first end-to-end task | |

### Implemented

- `aven/core/messages.py` - `UserMessage` / `AssistantMessage` /
  `ToolResultMessage` / `NoteMessage`, all nodes in a tree via `parent_id`;
  `to_llm()` projects them down to what the model sees; `to_dict` / `from_dict`
  round-trip them through plain JSON.
- `aven/core/session.py` - `Session`, an append-only JSONL file holding the
  message tree. `head` is the next append point, so branching is `checkout` plus
  `append` - one extra line, never a rewrite.
- `aven/core/events.py` - the frozen records the loop hands out.
- `aven/core/agent.py` - `run()`, a generator driving model call -> tool
  execution -> feed back, bounded by `max_turns`. The model function and the
  tools are injected, so the loop runs with no API key and a test can script a
  model's replies exactly.
- `aven/core/tools.py` - `@tool`, which derives the model-facing JSON schema
  from the function signature and makes each tool declare its risk
  (`read` / `reversible` / `irreversible`), how to preview a call before it
  runs, and how to undo it afterwards.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python examples/01_messages.py
.venv/bin/python -m pytest -q
```

No runtime dependencies yet, Python 3.11+.
