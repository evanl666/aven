"""The aven command: a personal assistant in the terminal.

    aven "sort out the invoices in my downloads"   one task, then review
    aven                                           the full-screen app
    aven -c                                        continuing the last session
    aven -r                                        pick a session from a list
    aven -p "what meetings do I have today"        answer on stdout, then exit
    aven --mode json "..." > events.jsonl          every event as one line
    aven -c --tree                                 the session tree, then exit

Everything a terminal app does that is not specific to being an assistant lives
in aven.terminal.app. What is here is what makes this one an assistant: the
prompt, the tools, and the one flag that opens Calendar and Mail.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
from pathlib import Path

from aven.apps.cli_assistant.mac import mac_tools
from aven.harness.connect import Connections, Connector
from aven.harness.toolbox import ToolBox
from aven.harness.vault import vault_for
from aven.terminal.app import Blueprint, Kit, launch
from aven.toolkit import file_tools, memory_tools, skill_tools
from aven.toolkit.connectors import build
from aven.toolkit.files import names_for

HOME = Path.home() / ".aven"
SESSIONS = HOME / "sessions"

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

You may only act inside the folders listed below, and the tools refuse any path
outside them. Bare paths resolve against the first one.

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


def arguments(parser: argparse.ArgumentParser) -> None:
    from aven.text import t

    parser.add_argument("--mac", action="store_true", help=t("cli.mac"))


def assemble(args: argparse.Namespace, roots: list[Path], found_skills: list) -> Kit:
    """The assistant's tools and the services it could reach.

    Files are what nearly every task touches, so they are always in play, across
    every root. The rest wait to be asked for: a calendar is dead weight while
    sorting a download folder, and its schema is charged for every turn it sits
    there.

    Two kinds of connector end up in the same list, which is the point of having
    one list. The local ones are built here and have nothing to sign in to. The
    rest come out of `connectors.toml` and may need a browser. A person looking
    at the Connections panel should not have to know which is which - only
    whether it works and what it can do.
    """
    # Memory is written relative to the working folder, not to all of them: a
    # fact belongs to one place or everywhere, and "everywhere" is the global
    # file rather than a second root.
    local: list[Connector] = [
        Connector(name="memory", about=GROUPS["memory"], tools=memory_tools(roots[0]))
    ]

    # Opt-in, not opt-out. These are the tools that reach outside the root and
    # ask macOS for permission, and forgetting a flag should not be what decides
    # whether Mail is reachable.
    if args.mac and platform.system() == "Darwin":
        mac = {tool.name: tool for tool in mac_tools()}
        local += [
            Connector(name="calendar", about=GROUPS["calendar"], tools=[
                mac["list_calendars"], mac["list_events"], mac["create_event"]]),
            Connector(name="mail", about=GROUPS["mail"], tools=[
                mac["draft_mail"], mac["send_mail"]]),
            Connector(name="search", about=GROUPS["search"], tools=[mac["spotlight"]]),
        ]

    configured, trouble = build(HOME, vault_for(HOME), extra=local)
    connections = Connections(configured)

    return Kit(
        box=ToolBox(
            core=file_tools(*roots) + skill_tools(found_skills),
            groups=connections.groups(),
            # So `use_tools` on a service nobody has signed in to comes back
            # with a sentence, rather than loading tools that fail on every call.
            gate=connections.gate,
        ),
        describe=connections.describe(),
        connections=connections,
        instructions=folders(roots),
        trouble=trouble,
    )


def folders(roots: list[Path]) -> str:
    """The folders, named, for the system prompt.

    The model cannot guess what a root is called, and with more than one the name
    is how it reaches the others. So the names come from the same function the
    tools use, rather than being written out twice and drifting apart.
    """
    named = names_for([Path(r).expanduser().resolve() for r in roots])
    lines = [
        f"- {name}  ({path})" + ("   <- the working folder" if n == 0 else "")
        for n, (name, path) in enumerate(named.items())
    ]
    if len(lines) == 1:
        return "The folder you may act in:\n\n" + lines[0]
    return (
        "The folders you may act in. A bare path means the working folder; reach "
        "another by putting its name first, as in "
        f"{list(named)[1]}/some/file.\n\n" + "\n".join(lines)
    )


def blueprint() -> Blueprint:
    """Built per call, so a test that redirects SESSIONS is read when we run."""
    return Blueprint(
        program="aven",
        description="cli.description",
        banner="cli.banner",
        system=SYSTEM,
        sessions=SESSIONS,
        assemble=assemble,
        arguments=arguments,
    )


def main(argv: list[str] | None = None) -> int:
    """The console entry point. asyncio.run is the only place the loop starts."""
    return asyncio.run(launch(blueprint(), argv))


if __name__ == "__main__":
    raise SystemExit(main())
