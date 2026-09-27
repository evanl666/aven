"""macOS actuators: Calendar, Mail and Spotlight, through osascript.

This is the rung of the ladder an agent running somewhere else cannot reach. An
agent in a VM needs your passwords in order to act as you; aven runs on your own
machine, so it asks macOS instead, and macOS asks you.

Arguments never go into the script text. AppleScript concatenates and evaluates
in one breath, so a subject line reading

    x" & (do shell script "rm -rf ~") & "

interpolated into a script runs that command. Everything here is passed out of
band via `on run argv`, which is to AppleScript what a bound parameter is to
SQL. The rule has no exceptions: if a value came from the model, from a file, or
from an email, it is an argument.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Annotated

from aven.harness.tools import Body, Tool, ToolResult, tool
from aven.text import t


class OsaError(Exception):
    """osascript refused, timed out, or the app said no."""


def _run(command: list[str], timeout: float) -> str:
    """The single point where aven shells out. Tests replace this."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OsaError(f"{command[0]} took longer than {timeout:g}s") from None
    if done.returncode != 0:
        raise OsaError(done.stderr.strip() or f"{command[0]} exited {done.returncode}")
    return done.stdout.strip()


def osa(script: str, *args: object, timeout: float = 60.0) -> str:
    """Run AppleScript, passing every value as an argument rather than as text.

    The script must be written as `on run argv ... end run`; `--` separates the
    script from the values so a value starting with a dash is still a value.
    """
    return _run(["osascript", "-e", script, "--", *(str(a) for a in args)], timeout)


# --- the scripts -----------------------------------------------------------
# Each one reads its values out of argv and never builds a string from them.

CALENDARS = """on run argv
    tell application "Calendar" to set names to name of calendars
    set AppleScript's text item delimiters to linefeed
    return names as text
end run"""

EVENTS = """on run argv
    set horizon to (item 1 of argv) as integer
    set fromDate to current date
    set toDate to fromDate + horizon * days
    set rows to {}
    tell application "Calendar"
        repeat with cal in calendars
            repeat with e in (every event of cal whose start date >= fromDate and start date <= toDate)
                set end of rows to ((start date of e) as string) & "  |  " & (summary of e) & "  |  " & (name of cal)
            end repeat
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return rows as text
end run"""

NEW_EVENT = """on run argv
    set when to current date
    set day of when to 1
    set year of when to (item 3 of argv) as integer
    set month of when to (item 4 of argv) as integer
    set day of when to (item 5 of argv) as integer
    set hours of when to (item 6 of argv) as integer
    set minutes of when to (item 7 of argv) as integer
    set seconds of when to 0
    set span to (item 8 of argv) as integer
    tell application "Calendar"
        tell calendar (item 1 of argv)
            set fresh to make new event with properties {summary:(item 2 of argv), start date:when, end date:when + span * minutes}
            return uid of fresh
        end tell
    end tell
end run"""

DROP_EVENT = """on run argv
    tell application "Calendar"
        tell calendar (item 1 of argv)
            delete (first event whose uid is (item 2 of argv))
        end tell
    end tell
    return "deleted"
end run"""

DRAFT = """on run argv
    tell application "Mail"
        set note to make new outgoing message with properties {subject:(item 2 of argv), content:(item 3 of argv), visible:false}
        tell note to make new to recipient at end of to recipients with properties {address:(item 1 of argv)}
        save note
        return (id of note) as string
    end tell
end run"""

DROP_DRAFT = """on run argv
    set wanted to (item 1 of argv) as integer
    tell application "Mail"
        repeat with note in messages of drafts mailbox
            if (id of note) is wanted then
                delete note
                return "deleted"
            end if
        end repeat
    end tell
    return "already gone"
end run"""

SEND = """on run argv
    tell application "Mail"
        set note to make new outgoing message with properties {subject:(item 2 of argv), content:(item 3 of argv), visible:false}
        tell note to make new to recipient at end of to recipients with properties {address:(item 1 of argv)}
        send note
    end tell
    return "sent"
end run"""


def mac_tools() -> list[Tool]:
    """The macOS toolset. No root to scope it to - macOS does the scoping."""

    @tool(risk="read")
    def spotlight(
        query: Annotated[str, "What to look for. Plain words search file contents and names"],
        limit: Annotated[int, "How many results at most"] = 20,
    ) -> str:
        """Search the whole disk with Spotlight, the way the magnifier does."""
        hits = _run(["mdfind", query], timeout=20.0).splitlines()
        if not hits:
            return "no matches"
        shown = hits[:limit]
        tail = "" if len(hits) <= limit else f"\n... and {len(hits) - limit} more"
        return "\n".join(shown) + tail

    @tool(risk="read")
    def list_calendars() -> str:
        """List the calendars, so an event can be filed in the right one."""
        return osa(CALENDARS) or "no calendars"

    @tool(risk="read")
    def list_events(
        days: Annotated[int, "How many days ahead to look"] = 7,
    ) -> str:
        """List upcoming calendar events."""
        return osa(EVENTS, days) or f"nothing in the next {days} days"

    @tool(risk="reversible", preview=lambda calendar, title, start, **_: t(
        "mac.event", calendar=calendar, title=title, start=start))
    def create_event(
        calendar: Annotated[str, "Which calendar, as list_calendars spells it"],
        title: Annotated[str, "Event title"],
        start: Annotated[str, "Start time, as 2026-09-21 14:30"],
        minutes: Annotated[int, "How long it lasts"] = 60,
    ) -> ToolResult:
        """Put an event on the calendar."""
        # Parsed here rather than in AppleScript: its date handling is a swamp,
        # and a bad string should fail before anything is created.
        when = datetime.fromisoformat(start)
        uid = osa(
            NEW_EVENT, calendar, title,
            when.year, when.month, when.day, when.hour, when.minute, minutes,
        )
        return ToolResult(
            output=f"created {title} at {when:%Y-%m-%d %H:%M} in {calendar}",
            undo=lambda: osa(DROP_EVENT, calendar, uid) and None,
        )

    @tool(risk="reversible",
          preview=lambda to, subject, **_: t("mac.draft", to=to, subject=subject),
          detail=lambda to, subject, body, **_: Body(
              title=f"{subject}  →  {to}", text=body))
    def draft_mail(
        to: Annotated[str, "Recipient address"],
        subject: Annotated[str, "Subject line"],
        body: Annotated[str, "The message"] = "",
    ) -> ToolResult:
        """Write an email and leave it in Drafts. Nothing is sent."""
        message_id = osa(DRAFT, to, subject, body)
        return ToolResult(
            output=f"drafted to {to}: {subject}",
            undo=lambda: osa(DROP_DRAFT, message_id) and None,
        )

    @tool(risk="irreversible",
          preview=lambda to, subject, **_: t("mac.send", to=to, subject=subject),
          detail=lambda to, subject, body, **_: Body(
              title=f"{subject}  →  {to}", text=body),
          # A sent mail cannot be recalled, and "always allow mail to my
          # accountant" is how the wrong draft goes out. Decided every time.
          pre_approvable=False)
    def send_mail(
        to: Annotated[str, "Recipient address"],
        subject: Annotated[str, "Subject line"],
        body: Annotated[str, "The message"] = "",
    ) -> str:
        """Send an email. It leaves the machine and does not come back."""
        osa(SEND, to, subject, body)
        return f"sent to {to}"

    return [spotlight, list_calendars, list_events, create_event, draft_mail, send_mail]
