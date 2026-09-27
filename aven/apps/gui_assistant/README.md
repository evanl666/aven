# gui_assistant — not built

A window rather than a terminal. Nothing here yet, and this file is what it
would take, so the folder is a decision rather than an empty gesture.

## What it already has

Everything below `apps/`. The loop yields events and has no opinion about who
draws them; the session tree, the staging tray, compaction, steering, skills,
memory and the policy layer are all reached the same way `cli_assistant` reaches
them. `terminal/app.py` is the shape of the wiring to copy — the same
Blueprint, a different presentation.

## What it needs

- **An event renderer that is not a terminal.** `terminal/render.py` and
  `terminal/shell.py` both consume the same `Event` stream; a third consumer is
  the whole of the port. Nothing in `harness/` has to change.
- **A tray surface.** The staging tray is the one part of aven a GUI would
  present better than a terminal does: a list with a checkbox per entry beats
  `[c] commit pending`. `TrayPanel` in `terminal/widgets.py` is the model to
  follow.
- **A toolkit choice.** Tauri or a local web view keeps the local-first promise.
  Electron does too, at a cost. Anything that renders remotely does not.
- **Nothing in the harness.** If building this needs a change below `apps/`,
  that change is a bug in the layering, not a requirement of the GUI — and
  `tests/test_architecture.py` will say so.

## What it does not need

A second agent, a second session format, or a second way of deciding what may
take effect. Those exist and are shared.
