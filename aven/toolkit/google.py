"""Google Calendar, as tools.

Written against the REST API directly rather than Google's client library. That
library brings a dependency tree larger than the rest of aven put together, for
three endpoints; and it has its own opinions about where credentials live, which
is the one decision this project has already made properly.

The interesting part is not the HTTP. It is `_risk_of_creating`:

    an event in your own calendar        reversible - undo deletes it again
    an event that mails an invitation    irreversible - nothing unsends mail

Same tool, same arguments, different answer depending on whether anybody else is
being told. A tool that declared one constant risk would have to pick: call it
reversible and invitations go out unapproved, or call it irreversible and adding
a reminder to your own day needs a decision. Neither is true, so it decides per
call - which is what `Tool.risk` being callable is for.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Annotated, Any

from aven.harness.tools import Body, Detail, Tool, ToolResult, tool
from aven.toolkit.oauth import Denied, Endpoints, OAuth

API = "https://www.googleapis.com/calendar/v3"

# Two narrow scopes rather than the one wide one. `calendar` would also grant
# the power to delete a calendar outright, which nothing here does and nobody
# should have to grant to add an event.
CALENDAR = Endpoints(
    authorize="https://accounts.google.com/o/oauth2/v2/auth",
    token="https://oauth2.googleapis.com/token",
    scopes=[
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ],
    # Google hands back a refresh token only when both of these are asked for,
    # and only on the first consent unless `prompt` forces the screen again.
    # Without one this stops working an hour after it starts, which is the worst
    # kind of broken: it demos perfectly.
    extra={"access_type": "offline", "prompt": "consent"},
)


def calendar_auth(client_id: str, client_secret: str, vault) -> OAuth:
    return OAuth(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        endpoints=CALENDAR,
        vault=vault,
    )


def calendar_tools(auth: OAuth) -> list[Tool]:
    """The tools, bound to a signed-in account.

    Built from the auth rather than from a token, so every call fetches a live
    one. A token captured here would be the one that was valid when the service
    was connected, and would expire in the middle of a conversation.
    """

    def call(
        method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> dict[str, Any]:
        url = f"{API}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )

        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {auth.token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                raw = answer.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as refused:
            detail = refused.read().decode(errors="replace")[:300]
            if refused.code in (401, 403):
                raise Denied(
                    f"Google refused this ({refused.code}). The connection may "
                    f"need renewing: {detail}"
                ) from refused
            raise RuntimeError(f"Google returned {refused.code}: {detail}") from refused
        except (urllib.error.URLError, TimeoutError) as unreachable:
            raise RuntimeError(f"could not reach Google: {unreachable}") from unreachable

    @tool(risk="read", preview="list the calendars on this Google account")
    def list_calendars() -> str:
        """Every calendar this account can see, with the id each one is called by."""
        found = call("GET", "/users/me/calendarList").get("items", [])
        if not found:
            return "no calendars on this account"
        return "\n".join(
            f"- {c.get('summary', '?')}  (id: {c.get('id')})"
            + ("   <- the default one" if c.get("primary") else "")
            for c in found
        )

    @tool(
        risk="read",
        preview=lambda days=7, calendar="primary", **_: (
            f"read the next {days} days from {calendar}"
        ),
    )
    def list_events(
        days: Annotated[int, "How many days ahead to look. 7 if unsure."] = 7,
        calendar: Annotated[str, "Calendar id, or 'primary'"] = "primary",
    ) -> str:
        """What is already in the calendar, soonest first."""
        now = datetime.now().astimezone()
        found = call(
            "GET",
            f"/calendars/{urllib.parse.quote(calendar)}/events",
            query={
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(days=days)).isoformat(),
                "singleEvents": "true",   # expand a repeating event into its occurrences
                "orderBy": "startTime",
                "maxResults": 50,
            },
        ).get("items", [])

        if not found:
            return f"nothing in {calendar} for the next {days} days"
        return "\n".join(_one_line(event) for event in found)

    @tool(
        risk=lambda **args: _risk_of_creating(**args),
        preview=lambda summary, start, invite="", **_: (
            f"put '{summary}' in the calendar at {start}"
            + (f" and email an invitation to {invite}" if invite else "")
        ),
        detail=lambda **args: _what_it_would_be(**args),
    )
    def create_event(
        summary: Annotated[str, "What the event is called"],
        start: Annotated[str, "When it starts, ISO 8601, e.g. 2026-10-02T15:00"],
        end: Annotated[str, "When it ends, ISO 8601. An hour after start if unsure."],
        calendar: Annotated[str, "Calendar id, or 'primary'"] = "primary",
        where: Annotated[str, "Location, if there is one"] = "",
        notes: Annotated[str, "Anything else that belongs in the description"] = "",
        invite: Annotated[
            str,
            "Email addresses to invite, comma separated. Leave empty for an "
            "event only this person sees - inviting someone sends them mail, "
            "which cannot be taken back.",
        ] = "",
    ) -> ToolResult:
        """Add an event. Invite somebody and they are mailed about it."""
        guests = _guests(invite)
        made = call(
            "POST",
            f"/calendars/{urllib.parse.quote(calendar)}/events",
            # Explicit rather than defaulted. Google's default for this is
            # "false" today, but an invitation that goes out because a default
            # changed is exactly the failure the risk taxonomy exists to stop.
            query={"sendUpdates": "all" if guests else "none"},
            body={
                "summary": summary,
                "location": where or None,
                "description": notes or None,
                "start": _moment(start),
                "end": _moment(end),
                "attendees": [{"email": g} for g in guests] or None,
            },
        )

        made_id = made.get("id", "")

        def undo() -> None:
            # Only reachable when nobody was invited - see _risk_of_creating -
            # so this really does put the world back.
            call(
                "DELETE",
                f"/calendars/{urllib.parse.quote(calendar)}/events/"
                f"{urllib.parse.quote(made_id)}",
                query={"sendUpdates": "none"},
            )

        told = f", invitation sent to {', '.join(guests)}" if guests else ""
        return ToolResult(
            output=f"added '{summary}' on {start}{told} (id {made_id})",
            undo=undo if made_id and not guests else None,
        )

    return [list_calendars, list_events, create_event]


# --- the judgement ------------------------------------------------------------


def _risk_of_creating(invite: str = "", **_: Any) -> str:
    """Reversible unless somebody else is told.

    Deleting an event you added puts your own calendar back exactly as it was.
    It does not unsend the mail that told four people to be somewhere, and it
    does not un-ring the notification on their phones. The second case is not a
    reversible action with an awkward undo; it is a different kind of act.
    """
    return "irreversible" if _guests(invite) else "reversible"


def _guests(invite: str) -> list[str]:
    return [who.strip() for who in (invite or "").split(",") if who.strip()]


def _what_it_would_be(
    summary: str = "", start: str = "", end: str = "", where: str = "",
    notes: str = "", invite: str = "", **_: Any,
) -> Detail | None:
    """The event as a person should read it before agreeing to it.

    A `Body`, because the thing being approved is a block of particulars - when,
    where, who - and one line cannot hold them. The guests come last and are
    named, since they are the part that makes this irreversible.
    """
    lines = [f"When:  {start} to {end}"]
    if where:
        lines.append(f"Where: {where}")
    if notes:
        lines.append(f"Notes: {notes}")
    guests = _guests(invite)
    if guests:
        lines.append("")
        lines.append(f"Emails an invitation to: {', '.join(guests)}")
        lines.append("Sent mail cannot be taken back.")
    return Body(title=summary, text="\n".join(lines))


# --- shapes Google wants ------------------------------------------------------


def _moment(written: str) -> dict[str, str]:
    """An ISO time, with a timezone Google will accept.

    A bare `2026-10-02T15:00` means nothing without one, and Google rejects it.
    The machine's own zone is the honest reading of what a person on that
    machine meant by three o'clock.
    """
    text = written.strip()
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        # Not parseable here: hand it over as-is and let Google say so, rather
        # than guessing at a format and silently booking the wrong hour.
        return {"dateTime": text}
    if when.tzinfo is None:
        when = when.astimezone()
    return {"dateTime": when.isoformat()}


def _one_line(event: dict[str, Any]) -> str:
    start = event.get("start", {})
    when = start.get("dateTime") or start.get("date", "?")
    where = event.get("location")
    return (
        f"- {when}  {event.get('summary', '(no title)')}"
        + (f"  @ {where}" if where else "")
    )
