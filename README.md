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
  model/       the only place a provider SDK is imported
  tools/       the tools that ship with aven, scoped to one root
  actuators/   native | shortcuts | osascript | browser
  cli/         a client of the daemon, nothing more
  daemon/      resident process + trigger bus
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
| 5 | Transaction layer: staging, commit, undo | **done** |
| 6 | Real model, first end-to-end task | **done** |
| 7 | CLI: file tools, event rendering, the review step | **done** |
| 8 | macOS actuators: Spotlight, Calendar, Mail | **done** |
| 9 | Streaming output and a spinner while waiting | **done** |
| 10 | Prompt caching, opt-in macOS tools, tidier undo | **done** |

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
- `aven/tx/tray.py` - `Tray`, which turns that declared risk into behaviour:
  reads run unrecorded, reversible work runs and keeps its undo, irreversible
  work is staged and the model is told so. The agent finishes its whole task
  without sending, paying or deleting; the user then reviews one batch and
  commits, discards, or rolls back newest-first.
- `aven/model/claude.py` - the only file importing a provider SDK. It satisfies
  `ModelFn` and nothing else: splits a response into text, tool calls and
  thinking blocks, keeps the latter whole for replay, maps stop reasons, and
  totals token usage. The client is injectable, so the adapter is tested with
  no key and no network.
- `aven/tools/files.py` - list / read / write / move / delete, every path
  resolved and checked against one root before anything happens. Deletion moves
  to an aven-owned trash, which is what makes it reversible.
- `aven/cli/` - `Renderer` turns events into terminal output and can do nothing
  else; a spinner runs from a thread while the loop is blocked on the network,
  since there is no other moment to draw in. `review.py` is the approval step;
  `main.py` wires a session, a root, a model and a tray behind the `aven`
  command.

A model may either return a finished `AssistantMessage` or be a generator that
yields text and returns the message at the end; `run()` handles both with a
`yield from`, so streaming is a property of the model rather than of the loop,
and a scripted test model stays one line long.
- `aven/actuators/mac.py` - Spotlight, Calendar and Mail through `osascript`.
  Values never enter the script text: AppleScript evaluates as it concatenates,
  so everything is passed out of band via `on run argv`, which is to AppleScript
  what a bound parameter is to SQL. This is also where the first irreversible
  tool lives, which is what makes the tray matter outside a demo.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q             # 75 tests, no key, no network

export ANTHROPIC_API_KEY=sk-ant-...
cd ~/some/folder
aven "把下载目录里的发票整理一下"          # one task, then review
aven                                       # keep talking
aven -c                                    # continue the last session
```

`--root` is the only directory the file tools may touch and defaults to the
current one. `--mac` adds Calendar, Mail and Spotlight, which macOS scopes itself and will
ask about the first time; they are opt-in because forgetting a flag should not
be what decides whether Mail is reachable. `AVEN_MODEL` sets the model.
Python 3.11+.
