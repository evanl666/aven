<h1 align="center">aven</h1>

<p align="center">
  <b>A local-first personal assistant that runs on your own machine.</b><br>
  Your files, your keys, your calendar — nothing leaves the laptop except the prompt.
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

A coding agent can be forgiven for acting first — a bad edit is a `git checkout`
away. An assistant moving your files, booking your appointments and sending your
mail cannot.

The cloud assistants answer this by running an agent in a VM somewhere, which
means it cannot reach your machine, which means it needs your passwords to act
as you. aven runs on your machine instead, so it asks macOS, and macOS asks you.

Everything else follows from that one choice.

| | Cloud assistant | aven |
|---|---|---|
| Where it runs | A VM you don't own | Your laptop |
| Your credentials | Vaulted, so the agent can log in as you | Never involved — the OS already knows who you are |
| Your files | Uploaded, or unreachable | Read in place, inside one directory you name |
| Permissions | Granted to the vendor | Granted by macOS, revocable in System Settings |
| Who sees your data | The vendor | The model, and only what you send it |

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
aven "one task"          # do it, show the batch, exit
aven                     # keep talking; Ctrl-D to leave
aven -c "and also..."    # continue the last session — has the context, and costs ~60% less
aven --mac "..."         # add Calendar, Mail and Spotlight (macOS will ask)
aven -v "..."            # show every tool result
```

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

## How it works

```
aven/
  core/       messages · session · events · tools · the loop
  tx/         the staging tray            ← where risk becomes behaviour
  model/      the only file importing a provider SDK
  tools/      file tools, scoped to one root
  actuators/  Calendar, Mail, Spotlight via osascript
  cli/        rendering, the review step, the command
```

**The loop is a generator.** `run()` yields events; the caller drives it with a
`for`, sees each step as it happens, and stops by not asking for the next one.
No subscriber list, no renderer that can take the agent down with it, and a test
reads as a plain list of what happened.

**The model is injected.** `core/` has no provider import. 106 tests run with no
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
5. **Triggers first, not chat first.** Planned. A turn can be opened by cron, a
   new mail, or a file landing in `~/Downloads`; `UserMessage.source` has carried
   that from the first commit.
6. **Repeated tasks crystallise into readable programs.** Planned. A task done
   twice becomes a routine you can read and edit, run deterministically with the
   model only at the ambiguous steps.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q       # 106 tests, no key, no network
```

The examples run without an API key and show one piece each:

```bash
.venv/bin/python examples/06_tray.py     # staging, commit, undo
.venv/bin/python examples/08_mac.py      # osascript with the calls faked out
.venv/bin/python examples/09_stream.py   # streaming and the spinner
```

## License

MIT
