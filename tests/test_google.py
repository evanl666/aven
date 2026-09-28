"""Google Calendar as tools.

The HTTP is not the interesting part and is not tested here. What is tested is
the judgement: when adding an event is something that can be taken back, and
when it is not.
"""

import json
import urllib.error
from datetime import datetime

import pytest

from aven.harness.tools import Body
from aven.harness.vault import Nowhere
from aven.toolkit.google import (
    _moment,
    _risk_of_creating,
    _what_it_would_be,
    calendar_auth,
    calendar_tools,
)


class Pretend:
    """Stands in for a signed-in OAuth, and records what would have gone out."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def token(self) -> str:
        return "a-token"


@pytest.fixture
def tools(monkeypatch):
    """The real tools, with only the network replaced."""
    sent: list[dict] = []

    def fake_urlopen(request, timeout=None):
        sent.append({
            "method": request.get_method(),
            "url": request.full_url,
            "body": json.loads(request.data.decode()) if request.data else None,
        })

        class Answer:
            def read(self):
                if "calendarList" in request.full_url:
                    return json.dumps({"items": [
                        {"summary": "Personal", "id": "me@example.com", "primary": True}
                    ]}).encode()
                if request.get_method() == "POST":
                    return json.dumps({"id": "made-it"}).encode()
                if request.get_method() == "DELETE":
                    return b""
                return json.dumps({"items": [
                    {"summary": "Standup", "start": {"dateTime": "2026-10-02T09:30:00+01:00"}}
                ]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Answer()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    built = {t.name: t for t in calendar_tools(Pretend())}
    built["_sent"] = sent
    return built


# --- the judgement -----------------------------------------------------------


def test_an_event_only_you_see_is_reversible():
    """Deleting it puts your own calendar back exactly as it was."""
    assert _risk_of_creating(invite="") == "reversible"
    assert _risk_of_creating() == "reversible"


def test_an_event_that_mails_somebody_is_irreversible():
    """Deleting the event does not unsend the mail that told four people to be
    somewhere, and does not un-ring the notification on their phones."""
    assert _risk_of_creating(invite="a@example.com") == "irreversible"
    assert _risk_of_creating(invite="a@example.com, b@example.com") == "irreversible"


def test_whitespace_is_not_an_invitation():
    """Otherwise a trailing comma from the model would make a private reminder
    look like something that cannot be taken back, and it would be staged for
    approval forever."""
    assert _risk_of_creating(invite="  ") == "reversible"
    assert _risk_of_creating(invite=",") == "reversible"


def test_the_tool_asks_per_call_rather_than_declaring_one_risk(tools):
    create = tools["create_event"]

    quiet = create.risk_for({"summary": "gym", "start": "x", "end": "y"})
    loud = create.risk_for({"summary": "review", "start": "x", "end": "y",
                            "invite": "boss@example.com"})

    assert (quiet, loud) == ("reversible", "irreversible")


# --- what a person is shown before deciding -----------------------------------


def test_the_detail_names_who_would_be_mailed():
    """The guests are the part that makes this irreversible, so they are the
    part that must not be summarised away."""
    shown = _what_it_would_be(
        summary="Review", start="2026-10-02T15:00", end="2026-10-02T16:00",
        invite="a@example.com, b@example.com",
    )

    assert isinstance(shown, Body)
    assert shown.title == "Review"
    assert "a@example.com" in shown.text and "b@example.com" in shown.text
    assert "cannot be taken back" in shown.text


def test_a_private_event_does_not_warn_about_mail_that_is_not_being_sent():
    shown = _what_it_would_be(summary="Gym", start="x", end="y")

    assert "cannot be taken back" not in shown.text
    assert "invitation" not in shown.text.lower()


def test_the_preview_says_out_loud_that_mail_would_go_out(tools):
    said = tools["create_event"].preview(
        {"summary": "Review", "start": "3pm", "invite": "a@example.com"}
    )

    assert "email an invitation to a@example.com" in said


# --- the shapes Google wants --------------------------------------------------


def test_a_time_with_no_zone_gets_this_machines_own():
    """A bare `2026-10-02T15:00` means nothing without one and Google rejects
    it. The machine's zone is the honest reading of what somebody sitting at it
    meant by three o'clock."""
    made = _moment("2026-10-02T15:00")

    assert datetime.fromisoformat(made["dateTime"]).tzinfo is not None
    assert made["dateTime"].startswith("2026-10-02T15:00")


def test_a_time_that_already_has_a_zone_is_left_alone():
    assert _moment("2026-10-02T15:00+09:00")["dateTime"] == "2026-10-02T15:00:00+09:00"


def test_something_unparseable_is_handed_over_rather_than_guessed_at():
    """Better for Google to refuse it than for us to book the wrong hour."""
    assert _moment("next tuesday")["dateTime"] == "next tuesday"


# --- what actually goes out ---------------------------------------------------


def test_a_private_event_explicitly_tells_google_not_to_notify(tools):
    """Google's default for this is 'false' today. An invitation that goes out
    because a default changed is exactly what the risk taxonomy exists to stop,
    so it is stated rather than assumed."""
    tools["create_event"](summary="Gym", start="2026-10-02T07:00", end="2026-10-02T08:00")

    assert "sendUpdates=none" in tools["_sent"][0]["url"]


def test_inviting_somebody_does_send_the_invitation(tools):
    tools["create_event"](summary="Review", start="2026-10-02T15:00",
                          end="2026-10-02T16:00", invite="a@example.com")

    sent = tools["_sent"][0]
    assert "sendUpdates=all" in sent["url"]
    assert sent["body"]["attendees"] == [{"email": "a@example.com"}]


def test_a_private_event_hands_back_an_undo_that_deletes_it(tools):
    made = tools["create_event"](summary="Gym", start="2026-10-02T07:00",
                                 end="2026-10-02T08:00")

    assert made.undo is not None
    made.undo()
    assert tools["_sent"][-1]["method"] == "DELETE"
    assert "made-it" in tools["_sent"][-1]["url"]


def test_an_event_with_guests_carries_no_undo_at_all(tools):
    """It is staged and approved rather than undone. Offering an undo that
    deletes the event would imply the mail could be taken back too."""
    made = tools["create_event"](summary="Review", start="2026-10-02T15:00",
                                 end="2026-10-02T16:00", invite="a@example.com")

    assert made.undo is None


def test_reading_the_calendar_needs_no_approval(tools):
    assert tools["list_events"].risk == "read"
    assert tools["list_calendars"].risk == "read"


# --- signing in ---------------------------------------------------------------


def test_the_scopes_asked_for_are_the_narrow_ones():
    """`calendar` would also grant the power to delete a calendar outright,
    which nothing here does and nobody should have to grant to add an event."""
    auth = calendar_auth("id", "secret", Nowhere())

    assert "https://www.googleapis.com/auth/calendar" not in auth.endpoints.scopes
    assert "https://www.googleapis.com/auth/calendar.events" in auth.endpoints.scopes


def test_offline_access_is_asked_for_so_the_connection_outlives_the_hour():
    auth = calendar_auth("id", "secret", Nowhere())

    assert auth.endpoints.extra["access_type"] == "offline"
