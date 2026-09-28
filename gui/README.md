# aven desktop

A window over `aven --mode rpc`. React and TypeScript inside a Tauri 2 shell,
which is one codebase for macOS, Windows, iOS and Android.

## Running it

```bash
# once
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Rust, for the shell
cd gui && npm install

# the dev sidecar: a wrapper pointing at the repo's venv
mkdir -p src-tauri/binaries
printf '#!/bin/sh\nexec "$(cd "$(dirname "$0")/../../.." && pwd)/.venv/bin/aven" "$@"\n' \
  > "src-tauri/binaries/aven-$(rustc -vV | awk '/host:/ {print $2}')"
chmod +x src-tauri/binaries/aven-*

export ANTHROPIC_API_KEY=sk-ant-...
npm run tauri dev
```

## How it is put together

```
src-tauri/src/lib.rs    the shell. Owns the pipes, knows nothing about the protocol
src/agent.ts            commands out, responses and events in, correlated by id
src/wire.ts             the protocol as types, transcribed from aven/wire/protocol.py
src/App.tsx             the one place events become interface state
src/Chat.tsx            the conversation
src/Approvals.tsx       what is waiting, one checkbox per line
src/Connections.tsx     which services it may reach
src/Detail.tsx          diffs, drafts, moves and orders
src/styles.css          every colour is a token; dark mode redefines them once
```

Three things about the design are load-bearing rather than incidental.

**The agent process outlives every command.** A staged call carries an unfired
closure over real paths and real functions. It cannot be serialised and cannot
outlive the process holding it, so restarting the agent between prompts would
throw away the tray — and the tray is what this window exists to show.

**A response is not ordered against the events.** aven starts a run before
writing the response that says it started, so `agent_start` can arrive first.
Every command carries an id and waits on a promise keyed by it; reading "the next
line" would resolve a prompt with somebody else's answer, intermittently.

**The input box unlocks on `settled`, not `agent_end`.** A follow-up in the
steering queue carries a run past `agent_end`.

## Not done yet

- **A folder picker.** `ROOTS` in `App.tsx` is hard-coded, and it is the only
  thing here a person cannot change without editing the source.
- **A real sidecar build.** The wrapper above is fine for development and cannot
  ship; a distributable needs a single-file build of aven (PyInstaller or
  similar) at `src-tauri/binaries/aven-<target-triple>`.
- **Sessions and the tree.** The protocol serves them (`sessions`, `tree`,
  `checkout`); nothing draws them.
- **Triggers.** Same: they run, and there is no pane for them.
- **Mobile.** Tauri 2 does iOS and Android from this codebase, but the agent is
  Python and cannot run on a phone. The phone has to talk to the Mac, which means
  a WebSocket transport beside the stdio one — `Conversation` in
  `aven/wire/rpc.py` takes an `emit` callback for exactly that reason and needs
  no changes.
