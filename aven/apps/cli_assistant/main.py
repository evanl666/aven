"""The aven command.

    aven "sort out the invoices in my downloads"   one task, then review
    aven                                     the full-screen app
    aven -c                                  the app, continuing the last session
    aven -r                                  pick a session from a list
    aven --plain                             keep talking, line by line
    aven -p "what meetings do I have today"       answer on stdout, then exit
    aven --mode json "..." > events.jsonl    every event as one line of JSON
    aven -c --tree                           show the session tree and exit

A client of the loop and nothing more. It owns the terminal; the loop owns the
work; the tray owns what actually takes effect.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import sys
import time
from pathlib import Path

from aven.terminal.render import BOLD, DIM, RED, Renderer
from aven.terminal.review import review
from aven.terminal.stream import Final, Jsonl
from aven.harness.agent import run
from aven.harness.compact import Compactor, estimate_tokens
from aven.harness.context import find, read
from aven.harness.skills import catalogue
from aven.harness.toolbox import ToolBox
from aven.harness.skills import find as find_skills
from aven.harness.messages import new_id
from aven.harness.session import Session
from aven.harness.sessions import Card, catalogue
from aven.harness.sessions import render as render_cards
from aven.harness.tree import render as render_tree
from aven import text as language
from aven.text import t
from aven.model import Claude
from aven.apps.cli_assistant.mac import mac_tools
from aven.toolkit import file_tools, memory_tools, skill_tools
from aven.harness.tx import Tray, bulk, guard, protect

SESSIONS = Path.home() / ".aven" / "sessions"


def note(text: str) -> None:
    """Commentary. Always stderr, so stdout can be the result and nothing else.

    Which files were read and what it cost are diagnostics even when a person
    is the one reading them - and when the caller is a script, a banner mixed
    into its input is a bug it has to work around.
    """
    print(text, file=sys.stderr)

# What each dormant group is for. The model reads this to decide whether a task
# needs the group, so it says when, not only what.
# What each dormant group is for. The model reads this to decide whether a task
# needs the group, so it says when, not only what. English, like every string
# the model reads.
GROUPS = {
    "memory": "Remember or forget lasting facts about this person. Take it when "
              "they say something that will still matter next time.",
    "calendar": "Read and create calendar events. Take it when they mention a "
                "meeting, their schedule, or a date or time.",
    "mail": "Draft and send mail. Take it when they want something sent to "
            "somebody.",
    "search": "Spotlight, across the whole disk. Take it when the file being "
              "looked for is not under the working folder.",
}

SYSTEM = """You are aven, a personal assistant running on this person's own computer.

Answer in the language they write to you in.

You may only act inside one folder, and every path is relative to it. The tools
refuse anything outside it.

That limit applies to the file tools. Calendar, Mail and Spotlight are managed by
macOS, which asks this person for access itself.

Your tools come in three kinds:
- read-only, usable at any time
- reversible, so go ahead: they can be rolled back whenever
- irreversible, which do not happen now - they join a queue and wait to be
  confirmed

"staged" in a tool result means that thing HAS NOT HAPPENED. Do not treat it as
done and do not keep reasoning as though it were. Finish whatever else you can,
then tell them what is waiting on them.

Not every tool is loaded at the start. Bring in a listed group with use_tools
when the task turns out to need it, one group at a time; a group stays once it is
in.

The memory group has remember and forget, for facts that OUTLAST THE TASK:
people, addresses, folder conventions, preferences. Not for details of what is
happening right now - the conversation already holds those. When they correct
you, forget the old fact before remembering the new one.

Get on with it rather than asking permission repeatedly. When you are done, say
what you did in a sentence or two."""


def preferred_language(argv: list[str] | None) -> str | None:
    """Read --lang out of argv before argparse gets a turn.

    The help strings are built while the parser is, which is before anything has
    been parsed - so a flag that picks the language of the help has to be found
    by hand, or `aven --lang en --help` prints it in the wrong one.
    """
    words = list(sys.argv[1:] if argv is None else argv)
    for n, word in enumerate(words):
        if word == "--lang" and n + 1 < len(words):
            return words[n + 1]
        if word.startswith("--lang="):
            return word.split("=", 1)[1]
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aven", description=t("cli.description"))
    parser.add_argument("prompt", nargs="?", help=t("cli.prompt"))
    parser.add_argument("-c", "--continue", dest="last", action="store_true",
                        help=t("cli.continue"))
    parser.add_argument("-r", "--resume", action="store_true",
                        help=t("cli.resume"))
    parser.add_argument("-n", "--name", help=t("cli.name"))
    parser.add_argument("--session", type=Path, help=t("cli.session"))
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help=t("cli.root"))
    parser.add_argument("--mac", action="store_true",
                        help=t("cli.mac"))
    parser.add_argument("--model", default=os.environ.get("AVEN_MODEL"),
                        help=t("cli.model"))
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help=t("cli.nocache"))
    parser.add_argument("--no-instructions", dest="instructions", action="store_false",
                        help=t("cli.noinstructions"))
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--bulk", type=int, default=25,
                        help=t("cli.bulk"))
    parser.add_argument("--protect", action="append", default=[], metavar=t("cli.protect_metavar"),
                        help=t("cli.protect"))
    parser.add_argument("--reserve", type=int, default=24_000,
                        help=t("cli.reserve"))
    parser.add_argument("--no-compact", dest="compact", action="store_false",
                        help=t("cli.nocompact"))
    parser.add_argument("-p", "--print", dest="oneshot", action="store_true",
                        help=t("cli.print"))
    parser.add_argument("--mode", choices=("text", "json"), default="text",
                        help=t("cli.mode"))
    parser.add_argument("--tree", action="store_true",
                        help=t("cli.tree"))
    parser.add_argument("--fork", metavar="id", nargs="?", const="",
                        help=t("cli.fork"))
    parser.add_argument("--lang", help=t("cli.lang"))
    parser.add_argument("-v", "--verbose", action="store_true", help=t("cli.verbose"))
    parser.add_argument("--plain", action="store_true",
                        help=t("cli.plain"))
    parser.add_argument("--yes", action="store_true",
                        help=t("cli.yes"))
    return parser


def fresh() -> Path:
    """A new session file.

    The timestamp leads so the directory sorts chronologically by name. The four
    characters after it are not decoration: a second is not fine-grained enough
    once `-p` exists, and a shell loop firing two one-shot runs inside the same
    second would otherwise have them share a file - two unrelated tasks
    branching one conversation.
    """
    return SESSIONS / f"{time.strftime('%Y%m%d-%H%M%S')}-{new_id()[:4]}.jsonl"


def pick_session(args: argparse.Namespace) -> Path | None:
    """Which file to talk to. None means they were asked and said no."""
    if args.session:
        return args.session

    SESSIONS.mkdir(parents=True, exist_ok=True)

    if args.resume:
        return choose(catalogue(SESSIONS))

    if args.last:
        existing = sorted(SESSIONS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if existing:
            return existing[-1]
        note(DIM(t("cli.no_sessions")))

    return fresh()


def choose(cards: list[Card]) -> Path | None:
    """Show the list and read a number.

    On stderr and stdin, never stdout: the list is a question, not a result, and
    a caller that redirected stdout is not the one being asked.
    """
    if not cards:
        note(DIM(t("cli.no_catalogue")))
        return fresh()

    note(render_cards(cards))
    note(BOLD(t("cli.pick")))
    try:
        typed = input().strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if typed.lower() in ("q", "quit", "exit"):
        return None
    if not typed:
        return cards[0].path
    if typed.isdigit() and 1 <= int(typed) <= len(cards):
        return cards[int(typed) - 1].path

    note(RED(t("cli.no_such", typed=typed)))
    return cards[0].path


async def turn(*, session: Session, prompt: str, model: Claude, tools, compactor,
               policy, args) -> None:
    """One prompt: run it, then decide what takes effect."""
    tray = Tray(policy=policy)
    screen = Renderer(verbose=args.verbose)
    screen.waiting(t("render.waiting"))

    async for event in run(
        session=session, prompt=prompt, model=model, tools=tools,
        tray=tray, compactor=compactor, max_turns=args.max_turns,
    ):
        screen.handle(event)

    print(DIM(f"\n  {model.usage}"))

    if args.yes:
        await asyncio.to_thread(tray.commit)
    else:
        await review(tray, session)


async def oneshot(*, session: Session, prompt: str, model: Claude, tools, compactor,
                  policy, args) -> int:
    """One prompt for a caller that is not watching the screen.

    No review step: review asks a person a question, and there is no person
    here. Staged work therefore stays staged unless --yes was passed, and the
    sink reports it either way - silently discarding an unsent email because
    nobody was around to confirm it would be the worse failure.
    """
    tray = Tray(policy=policy)
    sink = Jsonl() if args.mode == "json" else Final()

    async for event in run(
        session=session, prompt=prompt, model=model, tools=tools,
        tray=tray, compactor=compactor, max_turns=args.max_turns,
    ):
        sink.handle(event)

    committed = len(await asyncio.to_thread(tray.commit)) if args.yes else 0
    sink.close(tray, committed=committed)
    note(DIM(f"  {model.usage}"))
    return 0


async def _main(argv: list[str] | None = None) -> int:
    # Before the parser, because the parser's own help is text a person reads.
    language.use(preferred_language(argv))

    args = build_parser().parse_args(argv)

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(RED(t("cli.not_a_dir", path=root)), file=sys.stderr)
        return 1

    chosen = pick_session(args)
    if chosen is None:
        return 0
    session = Session.open(chosen)

    if args.name:
        session.rename(args.name)

    # Reading and copying a session needs no model, so these run before the key
    # check. Being locked out of your own transcript for want of an API key
    # would be absurd.
    if args.tree:
        print(render_tree(session))
        return 0

    if args.fork is not None:
        if not len(session):
            print(RED(t("cli.empty_fork")), file=sys.stderr)
            return 1
        SESSIONS.mkdir(parents=True, exist_ok=True)
        destination = SESSIONS / f"{time.strftime('%Y%m%d-%H%M%S')}-{new_id()[:4]}-fork.jsonl"
        try:
            forked = session.fork(destination, at=args.fork or None)
        except (KeyError, FileExistsError) as problem:
            print(RED(str(problem)), file=sys.stderr)
            return 1
        print(forked.path)
        note(DIM(t("cli.forked", n=len(forked))))
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(RED(t("cli.no_key")), file=sys.stderr)
        return 1

    policy = guard(bulk(limit=args.bulk), protect(*args.protect))

    found_skills = find_skills(root)

    # Files are what nearly every task touches, so they are always in play.
    # The rest wait to be asked for: a calendar is dead weight while sorting a
    # download folder, and its schema is charged for every turn it sits there.
    groups: dict[str, list] = {"memory": memory_tools(root)}

    # Opt-in, not opt-out. These are the tools that reach outside the root and
    # ask macOS for permission, and forgetting a flag should not be what
    # decides whether Mail is reachable.
    on_mac = args.mac and platform.system() == "Darwin"
    if on_mac:
        mac = {t.name: t for t in mac_tools()}
        groups["calendar"] = [mac["list_calendars"], mac["list_events"], mac["create_event"]]
        groups["mail"] = [mac["draft_mail"], mac["send_mail"]]
        groups["search"] = [mac["spotlight"]]

    box = ToolBox(core=file_tools(root) + skill_tools(found_skills), groups=groups)
    tools = box.active

    # Standing instructions are part of the system prompt, so they sit in the
    # cached prefix and cost nothing after the first turn.
    instruction_files = find(root) if args.instructions else []
    system = SYSTEM
    if found_skills:
        system += "\n\n" + catalogue(found_skills)
    waiting = box.catalogue(GROUPS)
    if waiting:
        system += "\n\n" + waiting
    if instruction_files:
        system = SYSTEM + "\n\n" + t("cli.instructions_header") + "\n\n" + read(
            instruction_files
        )

    picked = {"model": args.model} if args.model else {}
    model = Claude(tools=tools, system=system, cache=args.cache, **picked)

    compactor = None
    if args.compact:
        # The threshold is the model's own window less what the reply and the
        # turn's tool results will add before we look again. Asking the Models
        # API beats hard-coding a number that is wrong for every model but one.
        limit = await model.context_window() - args.reserve

        # A separate instance with no tools and no system prompt: summarising
        # needs neither, and handing them over would only make the request
        # bigger and invite the model to call something. Caching is off too -
        # the prefix it sends is the conversation being retired, read once and
        # never again, so a cache write would be paid at 1.25x and never read.
        compactor = Compactor(
            model=Claude(cache=False, **picked),
            limit=limit,
            # What the provider counted for the last request, which is exact.
            # It is one turn stale, and the reserve is what covers the gap.
            measure=lambda _msgs: getattr(model.usage, "last_input", 0),
        )

    waiting_note = t("cli.waiting_groups", n=len(box.dormant())) if box.dormant() else ""
    note(DIM(t("cli.banner", root=root, tools=len(box.active()),
                   waiting=waiting_note, session=session.path.name)))
    for path in instruction_files:
        # Read from the user's disk into the prompt: say so, every time.
        note(DIM(t("cli.read_instructions", path=path)))
    if found_skills:
        note(DIM(t("cli.skills", n=len(found_skills),
                   names=", ".join(s.name for s in found_skills))))

    if args.oneshot or args.mode == "json":
        prompt = piped(args.prompt)
        if not prompt:
            print(RED(t("cli.needs_prompt")), file=sys.stderr)
            return 1
        return await oneshot(session=session, prompt=prompt, model=model, tools=tools,
                             compactor=compactor, policy=policy, args=args)

    if args.prompt:
        await turn(session=session, prompt=args.prompt, model=model, tools=tools,
                   compactor=compactor, policy=policy, args=args)
        return 0

    if not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
        # Imported here so a one-shot `aven "..."` never pays for Textual.
        from aven.terminal.shell import Shell

        await Shell(
            session=session, model=model, tools=tools, root=root,
            compactor=compactor, policy=policy, max_turns=args.max_turns,
        ).run_async()
        return 0

    note(DIM(t("cli.talk")))
    while True:
        try:
            typed = await asyncio.to_thread(input, BOLD("› "))
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if typed.strip():
            await turn(session=session, prompt=typed.strip(), model=model, tools=tools,
                       compactor=compactor, policy=policy, args=args)


def piped(prompt: str | None) -> str:
    """The prompt, with anything piped in put before it.

    `git diff | aven -p "review this change"` should work: the pipe is the material
    and the argument says what to do with it, so the material goes first.
    """
    if sys.stdin.isatty():
        return prompt or ""
    piped_in = sys.stdin.read().strip()
    if not piped_in:
        return prompt or ""
    return f"{piped_in}\n\n{prompt}" if prompt else piped_in


def main(argv: list[str] | None = None) -> int:
    """The console entry point. asyncio.run is the only place the loop starts."""
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
