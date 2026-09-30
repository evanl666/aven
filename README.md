<h1 align="center">aven</h1>

<p align="center">
  <b>A local-first personal assistant that runs on your own machine.</b><br>
  Your files and your calendar stay here. Nothing irreversible happens without you.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#why">Why</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#design-commitments">Design</a> ·
  <a href="README.zh-CN.md">中文</a>
</p>

---

```
$ cd ~/Downloads && aven "把发票归档到 报销/,写一份清单"

⠋ 思考中

我先看看 Downloads 里有什么。
  · list_dir(path='.')  ✓
  · move_file(src='发票_滴滴_38.50元.pdf', dst='报销/发票_滴滴_38.50元.pdf')  ✓
  · move_file(src='发票_美团_126.00元.pdf', dst='报销/发票_美团_126.00元.pdf')  ✓
  · write_file(path='报销/清单.md', content=<173 字符>)  ✓

三张发票已归档,合计 463.50 元,清单在 报销/清单.md。

  4 次请求 · 输入 5914(缓存读 5103 · 写 803) · 输出 568

4 项已执行(可撤销)
  ✓  发票_滴滴_38.50元.pdf → 报销/发票_滴滴_38.50元.pdf
  ✓  发票_美团_126.00元.pdf → 报销/发票_美团_126.00元.pdf
  ✓  发票_京东_299.00元.pdf → 报销/发票_京东_299.00元.pdf
  ✓  write 报销/清单.md
[u] 撤销已执行   [Enter] 保持现状
>
```

One keystroke puts all of it back.

---

## Why

An assistant that moves your files, books your appointments and sends your mail
is holding things you cannot undo with a keystroke. So aven is built around one
question: **what happens in the moment before something irreversible happens?**

Three answers. Everything else in the design follows from them.

**It runs on your machine.** The file tools reach the folders you name and
nothing else — `../`, `/etc/passwd`, `~/.ssh` and symlinks pointing out are
refused at the tool boundary, not in the prompt. Calendar, Mail and Spotlight go
through macOS, which asks you for access itself and lets you take it back in
System Settings. aven stores no credentials for anything, because it needs none:
the operating system already knows who you are.

**Nothing irreversible happens without you.** Reversible work runs during the
turn and keeps its undo — asking permission to move a file that can be moved
back only teaches people to say yes without reading. Anything that cannot be
taken back is *staged*: described and not done, waiting in a list you approve one
line at a time, with the diff or the order or the draft under each line.

**You can read everything it did.** Every message, every tool call and every
argument is one line of JSON in a file you own. `aven --tree` walks the branches,
`--mode json` hands a whole run to another program, and nothing is compacted away
that you cannot go back and read in full.

One thing said plainly rather than buried: **the text of your conversation goes
to whichever model provider you configure.** Files are read locally and acted on
locally; the conversation is not. That is the one thing that leaves, and it
leaves only to the endpoint you chose.

## Quick start

```bash
git clone https://github.com/evanl666/aven && cd aven
python3 -m venv .venv && .venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/aven" ~/.local/bin/aven

export ANTHROPIC_API_KEY=sk-ant-...
export AVEN_MODEL=claude-sonnet-5        # optional; the default is claude-opus-5

cd ~/some/folder
aven "tidy this up"
```

`cd` is the permission model: the file tools can only reach the directory you
started in. Requests for `../`, `/etc/passwd`, `~/.ssh` or a symlink pointing
out are refused at the tool boundary, not in the prompt.

```bash
aven "one task"          # do it, show the batch, exit - pipes and redirects fine
aven                     # the full-screen app: live staging tray, Esc to interrupt
aven -c                  # continuing the last session (~60% cheaper per turn)
aven -r                  # pick a session from a list
aven --root ~/Downloads --root ~/Documents      # more than one folder in play
aven --mac "..."         # add Calendar, Mail and Spotlight (macOS will ask)
aven -p "what is on today"        # the answer on stdout, then exit - for scripts
aven --mode json "..."   # every event as one line of JSON
aven --mode rpc          # read commands on stdin: what a window or a phone drives
aven --watch             # fire the triggers in ~/.aven/triggers.toml on a clock
aven -c --tree           # the branches of this session, then exit
aven -v "..."            # show every tool result
```

Two files are yours to write, and aven only ever reads them:

```toml
# ~/.aven/triggers.toml - turns nobody typed
[[trigger]]
name = "morning"
at = "08:30"
prompt = "Summarise today's calendar and anything that arrived overnight."

[[trigger]]
watch = "~/Downloads"
prompt = "Something landed. File it if it is an invoice, leave it otherwise."
```

```toml
# ~/.aven/approvals.toml - decisions you already made, so you are not asked twice
[[approve]]
tool = "move_file"
when = "expenses/"
note = "filing invoices is fine, every month"
```

An approval names one tool and a fragment of the preview you read when you
decided. There is no wildcard, a tool can refuse to be pre-approved at all
(sending mail does), a call your own `--protect` rule warned about is always
asked, and every pre-approved run is marked in the transcript as not a fresh
decision.

Requires Python 3.11+ and macOS for the `--mac` tools. Everything else is
cross-platform.

## The review step

This is the part the whole design exists for. When a run finishes:

```
1 项等待确认
  ⏸  发送邮件给 finance@corp.com:9月报销
4 项已执行(可撤销)
  ✓  发票_滴滴.pdf → 报销/发票_滴滴.pdf
  ...
[c] 提交待确认   [d] 丢弃待确认   [u] 撤销已执行   [Enter] 保持现状
```

Two columns, because two kinds of action deserve different treatment:

- **✓ already done** — reversible work runs during the turn and keeps its undo.
  Asking permission to move a file that can be moved back only teaches people to
  say yes without reading.
- **⏸ waiting** — irreversible work never ran. The model was told it is staged,
  so it stops planning around an effect that has not happened, finishes
  everything else, and hands you one list.

`u` rolls the world back newest-first, and rolls the conversation back with it —
otherwise the model still believes the work stands.

The menu only offers keys that would do something. A tray holding one finished
calendar event shows `[u]` and nothing else.

## Connecting a service

A connector is a group of tools that stays out of the way until it is asked for.
Every tool's schema rides in every request, so holding the unused ones back is
worth about 40% of the per-turn cost — and it turns out to be the right shape
for permission too. A service that is off is not a greyed-out button; it is a
set of tools the model cannot see and therefore cannot call.

Services go in `~/.aven/connectors.toml`:

```toml
[google]
client_id = "....apps.googleusercontent.com"
client_secret = "...."

[mcp.notes]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Notes"]
about = "Read and write the notes folder"

[mcp.notes.risk]
read_text_file = "read"      # your judgement, not the server's
```

**Tokens go in the macOS login keychain**, through `security`, the same place
Safari keeps its own. On a platform with no keychain they go in a file only your
user can read, and the Connections panel says which — a product that quietly did
the weaker thing on one platform would be lying by omission. Nothing is
encrypted by aven itself: encryption needs a key, the key needs somewhere to
live, and somewhere to live is the problem being solved.

### Google Calendar

You need your own OAuth client, because a client id shipped in a public repo is
a client id everybody shares. Once, at
[console.cloud.google.com](https://console.cloud.google.com): enable the Google
Calendar API, then **Credentials → Create credentials → OAuth client ID →
Desktop app**. Paste the two values above.

Signing in opens your own browser. aven never sees your password — it listens on
`127.0.0.1` for the redirect, and the code that comes back is bound to a
one-time secret only this process knows (PKCE), so intercepting it is not enough
to spend it.

Then note which risk each tool carries:

| | |
|---|---|
| read the calendar | `read` — runs, never asks |
| add an event you alone see | `reversible` — runs, and undo deletes it again |
| add an event that invites somebody | `irreversible` — **waits for you** |

Same tool, different answer per call. Deleting an event puts your calendar back
exactly as it was; it does not unsend the mail that told four people to be
somewhere.

### Any MCP server

One server is one connector. This is the answer to "connect anything": writing a
connector by hand costs an OAuth flow and an API wrapper per service, and
pointing at an MCP server costs four lines.

`aven --connectors <anything>` searches the public MCP registry and prints the
config to paste:

```
$ aven --connectors stripe

com.stripe/mcp
published by stripe.com
MCP server integrating with Stripe - tools for customers, products, payments.

[mcp.stripe]
url = "https://mcp.stripe.com"
```

**It says who published it, and nothing more.** The name is a namespace whose
ownership the registry verified, so `stripe.com` is a fact; whether that is the
party you meant is yours to judge. A search for `stripe` also returns servers
published by strangers, and the difference is visible rather than labelled.

**The config is printed rather than written.** Pasting it is a deliberate act,
and what you paste is what you read — there is no button here that installs
somebody's server in one click. That matters because of the next paragraph.

**A local server is a program running as you.** The approval tray governs what
the model asks a server to do. It governs nothing about what the server's own
process does, which starts the moment it does: your files, your network, before
any tool is called. aven withholds credentials it holds — the API key it keeps
for itself is not passed on — but that is the limit of what it can do from
outside. Prefer a `url =` server where one exists: it runs on somebody else's
machine and sees only what you send it.

Two ways to reach one:

```toml
[mcp.files]                                  # a process here
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/Notes"]

[mcp.github]                                 # a server somewhere else
url = "https://api.githubcopilot.com/mcp/"
[mcp.github.headers]
Authorization = "Bearer ghp_..."
```

A remote url has to be `https`, unless it is loopback. Anything else would put
the tools' arguments, and the token authorising them, on the wire in clear.

**Every MCP tool is `irreversible` until you say otherwise**, so it is staged and
waits. That is deliberate. MCP lets a server describe its own tools as read-only
— a hint from the very party whose behaviour is in question, and a server that
deletes your files can claim to be harmless. This matters more for a remote
server, not less: a local one at least runs as you, on your machine. The two
ways out are both your own words: `trust = true` honours the server's hints, or
name a tool in `[…risk]` and give it a risk yourself. What you write always wins.

Tool names are prefixed with the server's (`files_read_file`), so an MCP tool can
never shadow aven's own — the ones with the folder sandbox around them.

A good way in: leave the risks alone for the first run. Every call goes to the
tray, so you can read what each tool actually wanted to do, with its arguments,
before deciding which of them are really read-only.

**On Google and other big names.** Searching a package registry for one returns a
dozen servers by a dozen strangers, and connecting one means handing that
stranger your account. aven's catalogue lists only servers published by whoever
makes the thing they talk to. For Google, use aven's own connector above: an
OAuth client you register yourself, talking to Google directly, with no third
party in between.

## How it works

```
aven/
  harness/    what an agent is, with no opinion about what it is for
    messages · session · sessions · tree      the record
    agent · steering · events · calling       the loop
    compact · context                         what fits in the window
    tools · toolbox · skills                  what it can do
    tx/                                       what takes effect  ← risk lives here
  model/      the only files importing a provider SDK
  toolkit/    tools more than one app wants: files, memory, skills
  terminal/   foundation every terminal app shares: rendering, review,
              the JSON and print sinks, widgets, the full-screen shell
  text/       what aven says, keyed; en is the source of truth, zh alongside
  apps/
    cli_assistant/   the personal assistant  (aven)
    cli_code/        the coding agent        (aven-code)
    gui_assistant/   not built yet
```

Each app is about a hundred lines: its system prompt, its tools, and any flag of
its own. Picking a session, building the compactor, printing the banner and
dispatching to one of six interfaces — the tree, a fork, print mode, JSON mode,
the full-screen shell, a line-by-line loop — is `terminal/app.py`, shared.

**The layering is one-directional, and tested.** An app may reach down to
anything; the foundation may reach down to the harness; the harness may reach
nowhere. `tests/test_architecture.py` reads the imports out of the source and
fails on any edge that points the wrong way — because folders alone isolate
nothing, and one convenient import undoes the arrangement without anybody
noticing until a second app needs it.

The harness also carries no text a person reads, in any language — enforced the
same way. What aven says lives in `text/`, keyed, with English as the source of
truth and the fallback, chosen by `AVEN_LANG`, the locale or `--lang`. What the
*model* reads is English in the source, next to the logic it steers: instructions
to a model are code, and a model answers in the language it was addressed in
whatever language it was instructed in.

**A tool may decide its risk from its own arguments.** Nearly all of them are one
risk always — `write_file` writes, `list_dir` reads. A shell is not: declaring it
irreversible makes `ls` wait for a keypress, and declaring it read is a lie the
tray cannot catch. So `run_command` judges each command, and a command the
allowlist does not recognise is staged like any other irreversible act. The
policy layer keeps its invariant either way — it may still only *raise* what the
tool decided.

**The loop is a generator.** `run()` yields events; the caller drives it with a
`for`, sees each step as it happens, and stops by not asking for the next one.
No subscriber list, no renderer that can take the agent down with it, and a test
reads as a plain list of what happened.

**The model is injected.** `harness/` has no provider import. 551 tests run with no
API key and no network, and swapping providers does not touch the loop.

**Tools declare four things at the definition site** — the JSON schema (derived
from the signature, so it cannot drift), how risky they are, how to preview a
call before it runs, and how to undo it afterwards:

```python
@tool(risk="reversible", preview="{src} → {dst}")
def move_file(
    src: Annotated[str, "Current path, relative to the root"],
    dst: Annotated[str, "New path, relative to the root"],
) -> ToolResult:
    """Move or rename a file."""
    source, target = inside(src), inside(dst)      # both checked against the root
    shutil.move(source, target)
    return ToolResult(
        output=f"moved {show(source)} → {show(target)}",
        undo=lambda: shutil.move(target, source),  # closes over where it actually went
    )
```

Only the tool knows what it really did — the path it settled on after a rename
collision, the id Calendar handed back — so only the tool can build the undo.

**Sessions are append-only JSONL.** One message per line, the tree carried by
`parent_id`, nothing ever rewritten. Branching is a pointer move. That
discipline is what makes undo possible: going back never erases.

## Design commitments

Decided up front, because retrofitting any of them is painful.

1. **Side effects are staged, not fired.** ✅ Shipped — see the review step.
2. **Untrusted content never authorises an action.** Partly shipped: paths are
   resolved before the boundary check, and AppleScript values are passed
   out-of-band via `on run argv` — a subject line reading
   `x" & (do shell script "rm -rf ~") & "` stays a subject line.
3. **Tiered actuation.** Partly shipped: native → `osascript` today; Shortcuts,
   Accessibility and vision clicking are the rungs below.
4. **The model gets handles, not values.** Planned. Private data becomes
   `<<person:7>>` before the prompt leaves the machine, dereferenced only inside
   the tool executor. A hijacked model cannot leak what was never in its context.
5. **Triggers first, not chat first.** ✅ Shipped — a turn can be opened by a
   time of day or a folder gaining a file, and `UserMessage.source` records which.
   The full-screen app fires them while it is up, so the tray keeps what happened
   while you were away; `--watch` is the headless half.
6. **Repeated tasks crystallise into readable programs.** Planned. Memory does
   this for facts already - what is learned lands in Markdown the person owns. A task done
   twice becomes a routine you can read and edit, run deterministically with the
   model only at the ambiguous steps.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q       # 551 tests, no key, no network
```

The examples run without an API key and show one piece each:

```bash
.venv/bin/python examples/06_tray.py     # staging, commit, undo
.venv/bin/python examples/08_mac.py      # osascript with the calls faked out
.venv/bin/python examples/09_stream.py   # streaming and the spinner
.venv/bin/python examples/13_rpc.py      # a client driving aven over a pipe
sh examples/14_modes.sh                  # the four interfaces, same task
```

## License

MIT
