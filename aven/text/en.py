"""The English catalogue, and the source of truth for every key.

A key that is not here is a bug: `t()` falls back to this file, so anything
missing from it has nowhere left to fall. Keys are `<area>.<thing>`, area first,
so everything one screen says sits together.
"""

from __future__ import annotations

TEXT: dict[str, str] = {
    # --- the staging tray --------------------------------------------------
    "tray.empty": "nothing changed",
    "tray.summary": "{pending} waiting for approval / {applied} done, undoable",
    "tray.state.pending": "needs approval",
    "tray.state.applied": "undoable",
    "tray.staged": "staged",
    "tray.staged.long": "staged, waiting for you",
    "tray.none_yet": "nothing changed yet",
    "tray.title": "Staging",
    "tray.pending_count": "{n} waiting for approval\n",
    "tray.undoable_count": "{n} done (undoable)\n",
    "tray.button.commit": "Commit",
    "tray.button.discard": "Discard",
    "tray.button.undo": "Undo",
    # --- policy verdicts ---------------------------------------------------
    "policy.bulk": "{n} changes already this run - worth a look before more",
    "policy.protect": "the path contains {pattern!r}; think before changing this",
    # --- bringing tools in -------------------------------------------------
    "toolbox.load": "load the {group} tools",
    # --- the session tree --------------------------------------------------
    "tree.empty": "this session is empty",
    "tree.footer": (
        "{nodes} nodes, {branches} branches. /tree <id> to go back, "
        "/fork <id> to save one out"
    ),
    "tree.who.user": "you",
    "tree.who.assistant": "aven",
    "tree.who.summary": "summary",
    # --- listing sessions --------------------------------------------------
    "sessions.untitled": "(empty session)",
    "sessions.none": "no sessions yet",
    "sessions.line": "{when} · {messages} messages · {file}",
    "ago.second": "{n}s ago",
    "ago.minute": "{n}m ago",
    "ago.hour": "{n}h ago",
    "ago.day": "{n}d ago",
    # --- usage -------------------------------------------------------------
    "usage.nocache": "no cache",
    "usage.cache": "cache read {read} · wrote {written}",
    "usage.line": "{requests} requests · in {input} ({cache}) · out {output}",
    # --- tool previews -----------------------------------------------------
    "files.edit": "edit {path}: {old}",
    "files.delete": "delete {path}",
    "memory.where.global": "(everywhere)",
    "memory.where.here": "(this folder)",
    "memory.remember": "remember {where}: {fact}",
    "memory.forget": "forget {where}: {fact}",
    "skills.read": "read skill {name}",
    "mac.event": "calendar {calendar!r}, new: {title} @ {start}",
    "mac.draft": "draft to {to}: {subject}",
    "mac.send": "send mail to {to}: {subject}",
    # --- what the line-by-line renderer says -------------------------------
    "render.compacted": (
        "the conversation got long; the earlier part is now a summary "
        "(every word is still in the session file)"
    ),
    "render.max_turns": "hit the turn limit, and the task is not finished",
    "render.truncated": "the reply was cut off. Ask it to finish, or split the task up",
    "render.waiting": "thinking",
    "render.pending_header": "{n} waiting for approval",
    "render.undoable_header": "{n} done (undoable)",
    "render.outcome": "  {verb} {n}",
    "render.no_change": "  nothing changed",
    "render.clipped": "<{n} characters>",
    # --- the review step ---------------------------------------------------
    "review.commit": "[c] commit pending",
    "review.discard": "[d] discard pending",
    "review.undo": "[u] undo what ran",
    "review.keep": "[Enter] leave it",
    "review.unknown": "  there is no [{choice}] here",
    "review.committed": "committed",
    "review.discarded": "discarded",
    "review.undone": "undone",
    "review.rewound": "  the conversation went back to before the change too",
    # --- the print and json sinks ------------------------------------------
    "stream.staged": "not run (waiting for approval): {preview}",
    # --- the full-screen shell ---------------------------------------------
    "shell.help": """\
/session   what this session is
/name x    name this session, so -r can find it
/tree      the branches; /tree <id> goes back to one
/fork      save this branch out as its own session; /fork <id> truncates
/undo      undo what ran (the conversation goes back too)
/commit    run what is waiting for approval
/discard   drop what is waiting for approval
/cost      what this session has cost
/clear     clear the screen (the session file is untouched)
/quit      leave

Esc interrupts · Ctrl+T shows the staging panel · Ctrl+Q quits""",
    "shell.bind.interrupt": "interrupt",
    "shell.bind.tray": "staging",
    "shell.bind.clear": "clear",
    "shell.bind.quit": "quit",
    "shell.placeholder": "say something · /help for commands",
    "shell.queued": "queued ({n}) - it goes in when this turn finishes",
    "shell.no_usage": "no usage recorded",
    "shell.unknown_command": "there is no {name} command; /help lists them",
    "shell.interrupted": "interrupted. What ran and can be undone is still in staging.",
    "shell.error": "something went wrong: {kind}: {problem}",
    "shell.busy": "still running - press Esc first",
    "shell.moved": "back at {id}. Say something and a new branch starts here.",
    "shell.carrying": "carrying over what that branch found out...",
    "shell.carry_failed": "could not carry it over ({kind}); the branch move is done",
    "shell.carried": "carried over: {gist}",
    "shell.unnamed": "this session has no name",
    "shell.named": "this session is now {name!r}",
    "shell.describe.name": "name: {name}",
    "shell.describe.none": "(unnamed)",
    "shell.describe.file": "file: {path}",
    "shell.describe.messages": "messages: {n} · {branches} branches",
    "shell.describe.head": "at: {id}",
    "shell.describe.usage": "usage: {usage}",
    "shell.forked": "forked to {file} ({n} messages). The original is untouched.",
    "shell.committed": "committed {n}",
    "shell.commit_failed": "; {preview} failed: {output}",
    "shell.discarded": "discarded {n}; they never happened",
    "shell.undone": "undid {n}, and the conversation went back with it",
    "shell.tools": "{n} tools",
    # --- the command line --------------------------------------------------
    "cli.description": "a local-first personal assistant",
    "cli.prompt": "what to do. Leave it out for a conversation",
    "cli.continue": "carry on from the most recent session",
    "cli.resume": "list the sessions and pick one",
    "cli.name": "name this session, so it is easy to find later",
    "cli.session": "use this session file",
    "cli.root": "a folder the file tools may touch; repeatable, and the first is the working one. Defaults to the current folder",
    "cli.mac": "turn on Calendar / Mail / Spotlight (macOS will ask you for access)",
    "cli.model": "model id; AVEN_MODEL works too",
    "cli.nocache": "turn off prompt caching (for debugging)",
    "cli.noinstructions": "do not load AVEN.md / AGENTS.md",
    "cli.bulk": "show you the batch once a run changes more than this many things",
    "cli.protect": "a path containing this needs confirming; repeatable",
    "cli.protect_metavar": "fragment",
    "cli.reserve": "tokens held back for the reply and the next turn's growth",
    "cli.nocompact": "turn off automatic compaction",
    "cli.print": "run it and exit, writing only the final answer to stdout (for scripts)",
    "cli.mode": "text for a person; json writes every event to stdout as JSONL",
    "cli.tree": "print this session's message tree and exit",
    "cli.fork": "save the session out as a new file, optionally truncated at a node",
    "cli.verbose": "show every tool result",
    "cli.plain": "no full-screen interface; talk line by line",
    "cli.yes": "skip the review and commit (for automation; be careful)",
    "cli.lang": "interface language, e.g. en or zh",
    "cli.no_sessions": "no earlier session found; starting a new one",
    "cli.no_catalogue": "no sessions on record; starting a new one",
    "cli.pick": "pick one (Enter = the most recent, q = quit):",
    "cli.no_such": "there is no item {typed}; using the most recent",
    "cli.not_a_dir": "not a directory: {path}",
    "cli.empty_fork": "this session is empty; there is nothing to fork",
    "cli.forked": "  {n} messages, and the original was not touched",
    "cli.no_key": "set ANTHROPIC_API_KEY first",
    "cli.instructions_header": (
        "Below are the standing instructions this person wrote. They take "
        "precedence over the general guidance above."
    ),
    "cli.banner": "aven · {root} · {tools} tools{waiting} · {session}",
    "cli.waiting_groups": " + {n} groups not loaded",
    "cli.read_instructions": "  ↳ read instructions from {path}",
    "cli.skills": "  ↳ {n} skills available: {names}",
    "cli.needs_prompt": "-p / --mode json needs a prompt (an argument or a pipe)",
    "cli.talk": "say something; Ctrl-D to leave\n",
    # --- the coding app ----------------------------------------------------
    "code.description": "a coding agent on the same harness",
    "code.banner": "aven-code · {root} · {tools} tools{waiting} · {session}",
    "code.allow": (
        "treat this command as read-only, so it runs without asking; repeatable"
    ),
    "code.run": "run: {command}",
    "code.grep": "search for {pattern!r}",
    "code.glob": "list files matching {pattern!r}",
    # --- an order, the one preview a line is least adequate for -------------
    "order.total": "total  —  {total}",
    "order.where": "from  —  {value}",
    "order.account": "as  —  {value}",
    "order.arrives": "arrives  —  {value}",
    "cli.ask_every_time": "ignore the standing approvals for this run",
    "cli.standing": "  ↳ {n} standing approvals in force:",
    # --- turns nobody typed -------------------------------------------------
    "cli.watch": "fire the triggers on a clock until interrupted",
    "cli.every": "how often to look, in seconds",
    "cli.no_triggers": "no triggers to fire. Write some in {path}",
    "cli.watching": "  ↳ watching {n} triggers, looking every {every}s:",
    "cli.firing": "  ↳ firing {name}",
    "cli.stopped_watching": "  ↳ stopped watching",
    "shell.fired": "⏰ {name} fired",
}
