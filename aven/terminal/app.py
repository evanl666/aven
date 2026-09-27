"""Everything a terminal app does that is not specific to what it is for.

Both apps pick a session, build a compactor, print a banner, and then dispatch to
one of five interfaces - the tree, a fork, print mode, JSON mode, the full-screen
shell or a line-by-line loop. None of that has an opinion about whether the agent
is sorting invoices or reading a codebase, and having it twice would mean fixing
every bug in it twice.

What an app supplies is a Blueprint: its name, its system prompt, the tools it
assembles, and any flags of its own. What it gets back is `launch`.

The Blueprint is built per call rather than at import, so a test that redirects
where sessions live is read when the app runs and not when the module loaded.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aven.harness.agent import run
from aven.harness.compact import Compactor
from aven.harness.context import find as find_instructions
from aven.harness.context import read as read_instructions
from aven.harness.messages import new_id
from aven.harness.session import Session
from aven.harness.sessions import Card, catalogue
from aven.harness.sessions import render as render_cards
from aven.harness.skills import catalogue as skill_catalogue
from aven.harness.skills import find as find_skills
from aven.harness.toolbox import ToolBox
from aven.harness.tree import render as render_tree
from aven.harness.tx import Tray, bulk, guard, protect
from aven.model import Claude
from aven.terminal.render import BOLD, DIM, RED, Renderer
from aven.terminal.review import review
from aven.terminal.stream import Final, Jsonl
from aven import text as language
from aven.text import t


def note(message: str) -> None:
    """Commentary. Always stderr, so stdout can be the result and nothing else.

    Which files were read and what it cost are diagnostics even when a person is
    the one reading them - and when the caller is a script, a banner mixed into
    its input is a bug it has to work around.
    """
    print(message, file=sys.stderr)


@dataclass(frozen=True, kw_only=True)
class Kit:
    """What an app decided its agent can do.

    `describe` is read by the model, not by a person: it is how a dormant group
    says when it is worth bringing in, so it belongs beside the tools rather than
    in the text catalogue.
    """

    box: ToolBox
    describe: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Blueprint:
    """One terminal app, as the parts that differ from every other one."""

    program: str
    description: str  # a text key
    banner: str  # a text key
    system: str
    sessions: Path
    assemble: Callable[[argparse.Namespace, Path, list], Kit]
    arguments: Callable[[argparse.ArgumentParser], None] | None = None


# --- the command line --------------------------------------------------------


def preferred_language(argv: list[str] | None) -> str | None:
    """Read --lang out of argv before argparse gets a turn.

    The help strings are built while the parser is, which is before anything has
    been parsed - so a flag that picks the language of the help has to be found
    by hand, or `--lang en --help` prints it in the wrong one.
    """
    words = list(sys.argv[1:] if argv is None else argv)
    for n, word in enumerate(words):
        if word == "--lang" and n + 1 < len(words):
            return words[n + 1]
        if word.startswith("--lang="):
            return word.split("=", 1)[1]
    return None


def build_parser(blueprint: Blueprint) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=blueprint.program, description=t(blueprint.description)
    )
    parser.add_argument("prompt", nargs="?", help=t("cli.prompt"))
    parser.add_argument("-c", "--continue", dest="last", action="store_true",
                        help=t("cli.continue"))
    parser.add_argument("-r", "--resume", action="store_true", help=t("cli.resume"))
    parser.add_argument("-n", "--name", help=t("cli.name"))
    parser.add_argument("--session", type=Path, help=t("cli.session"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help=t("cli.root"))
    parser.add_argument("--model", default=os.environ.get("AVEN_MODEL"),
                        help=t("cli.model"))
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help=t("cli.nocache"))
    parser.add_argument("--no-instructions", dest="instructions", action="store_false",
                        help=t("cli.noinstructions"))
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--bulk", type=int, default=25, help=t("cli.bulk"))
    parser.add_argument("--protect", action="append", default=[],
                        metavar=t("cli.protect_metavar"), help=t("cli.protect"))
    parser.add_argument("--reserve", type=int, default=24_000, help=t("cli.reserve"))
    parser.add_argument("--no-compact", dest="compact", action="store_false",
                        help=t("cli.nocompact"))
    parser.add_argument("-p", "--print", dest="oneshot", action="store_true",
                        help=t("cli.print"))
    parser.add_argument("--mode", choices=("text", "json"), default="text",
                        help=t("cli.mode"))
    parser.add_argument("--tree", action="store_true", help=t("cli.tree"))
    parser.add_argument("--fork", metavar="id", nargs="?", const="", help=t("cli.fork"))
    parser.add_argument("--lang", help=t("cli.lang"))
    parser.add_argument("-v", "--verbose", action="store_true", help=t("cli.verbose"))
    parser.add_argument("--plain", action="store_true", help=t("cli.plain"))
    parser.add_argument("--yes", action="store_true", help=t("cli.yes"))

    if blueprint.arguments is not None:
        blueprint.arguments(parser)
    return parser


# --- which session -----------------------------------------------------------


def fresh(sessions: Path) -> Path:
    """A new session file.

    The timestamp leads so the directory sorts chronologically by name. The four
    characters after it are not decoration: a second is not fine-grained enough
    once `-p` exists, and a shell loop firing two one-shot runs inside the same
    second would otherwise have them share a file - two unrelated tasks
    branching one conversation.
    """
    return sessions / f"{time.strftime('%Y%m%d-%H%M%S')}-{new_id()[:4]}.jsonl"


def pick_session(args: argparse.Namespace, sessions: Path) -> Path | None:
    """Which file to talk to. None means they were asked and said no."""
    if args.session:
        return args.session

    sessions.mkdir(parents=True, exist_ok=True)

    if args.resume:
        return choose(catalogue(sessions), sessions)

    if args.last:
        existing = sorted(sessions.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if existing:
            return existing[-1]
        note(DIM(t("cli.no_sessions")))

    return fresh(sessions)


def choose(cards: list[Card], sessions: Path) -> Path | None:
    """Show the list and read a number.

    On stderr and stdin, never stdout: the list is a question, not a result, and
    a caller that redirected stdout is not the one being asked.
    """
    if not cards:
        note(DIM(t("cli.no_catalogue")))
        return fresh(sessions)

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


# --- one prompt --------------------------------------------------------------


async def turn(*, session, prompt, model, tools, compactor, policy, args) -> None:
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


async def oneshot(*, session, prompt, model, tools, compactor, policy, args) -> int:
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


def piped(prompt: str | None) -> str:
    """The prompt, with anything piped in put before it.

    `git diff | aven -p "review this change"` should work: the pipe is the
    material and the argument says what to do with it, so the material goes
    first.
    """
    if sys.stdin.isatty():
        return prompt or ""
    piped_in = sys.stdin.read().strip()
    if not piped_in:
        return prompt or ""
    return f"{piped_in}\n\n{prompt}" if prompt else piped_in


# --- the whole thing ---------------------------------------------------------


async def launch(blueprint: Blueprint, argv: list[str] | None = None) -> int:
    # Before the parser, because the parser's own help is text a person reads.
    language.use(preferred_language(argv))

    args = build_parser(blueprint).parse_args(argv)

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(RED(t("cli.not_a_dir", path=root)), file=sys.stderr)
        return 1

    chosen = pick_session(args, blueprint.sessions)
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
        return _fork(session, args, blueprint.sessions)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(RED(t("cli.no_key")), file=sys.stderr)
        return 1

    policy = guard(bulk(limit=args.bulk), protect(*args.protect))

    found_skills = find_skills(root)
    kit = blueprint.assemble(args, root, found_skills)
    box = kit.box
    tools = box.active

    # Standing instructions are part of the system prompt, so they sit in the
    # cached prefix and cost nothing after the first turn.
    instruction_files = find_instructions(root) if args.instructions else []
    system = blueprint.system
    if found_skills:
        system += "\n\n" + skill_catalogue(found_skills)
    waiting = box.catalogue(kit.describe)
    if waiting:
        system += "\n\n" + waiting
    if instruction_files:
        system += "\n\n" + t("cli.instructions_header") + "\n\n" + read_instructions(
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
    note(DIM(t(blueprint.banner, root=root, tools=len(box.active()),
                waiting=waiting_note, session=session.path.name)))
    for path in instruction_files:
        # Read from the user's disk into the prompt: say so, every time.
        note(DIM(t("cli.read_instructions", path=path)))
    if found_skills:
        note(DIM(t("cli.skills", n=len(found_skills),
                   names=", ".join(s.name for s in found_skills))))

    running = dict(session=session, model=model, tools=tools,
                   compactor=compactor, policy=policy, args=args)

    if args.oneshot or args.mode == "json":
        prompt = piped(args.prompt)
        if not prompt:
            print(RED(t("cli.needs_prompt")), file=sys.stderr)
            return 1
        return await oneshot(prompt=prompt, **running)

    if args.prompt:
        await turn(prompt=args.prompt, **running)
        return 0

    if not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
        # Imported here so a one-shot run never pays for Textual.
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
            await turn(prompt=typed.strip(), **running)


def _fork(session: Session, args: argparse.Namespace, sessions: Path) -> int:
    if not len(session):
        print(RED(t("cli.empty_fork")), file=sys.stderr)
        return 1

    sessions.mkdir(parents=True, exist_ok=True)
    destination = sessions / f"{time.strftime('%Y%m%d-%H%M%S')}-{new_id()[:4]}-fork.jsonl"
    try:
        forked = session.fork(destination, at=args.fork or None)
    except (KeyError, FileExistsError) as problem:
        print(RED(str(problem)), file=sys.stderr)
        return 1

    print(forked.path)
    note(DIM(t("cli.forked", n=len(forked))))
    return 0
