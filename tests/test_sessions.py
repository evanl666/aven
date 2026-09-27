"""Tests for naming a session and for listing the ones on disk."""

from pathlib import Path

from aven.harness.messages import AssistantMessage, UserMessage
from aven.harness.session import Session
from aven.harness.sessions import card, catalogue, render


def talk(path, *texts, name=None):
    session = Session.open(path)
    if name:
        session.rename(name)
    for text in texts:
        session.append(UserMessage(text=text))
        session.append(AssistantMessage(text="好", stop_reason="end_turn"))
    return session


# --- the name ----------------------------------------------------------------


def test_a_session_has_no_name_until_someone_gives_it_one(tmp_path):
    assert talk(tmp_path / "s.jsonl", "问题").name is None


def test_the_last_name_on_the_path_wins(tmp_path):
    session = talk(tmp_path / "s.jsonl", "问题")
    session.rename("发票")
    session.rename("发票整理 2026-07")

    assert session.name == "发票整理 2026-07"


def test_a_name_survives_reopening_the_file(tmp_path):
    talk(tmp_path / "s.jsonl", "问题", name="发票整理")

    assert Session.open(tmp_path / "s.jsonl").name == "发票整理"


def test_renaming_is_undone_by_going_back(tmp_path):
    """It is an entry, so it is branch-relative like everything else."""
    session = talk(tmp_path / "s.jsonl", "问题")
    before = session.head
    session.rename("新名字")

    session.checkout(before)

    assert session.name is None


def test_the_name_is_never_shown_to_the_model(tmp_path):
    from aven.harness.messages import to_llm

    session = talk(tmp_path / "s.jsonl", "问题", name="发票整理")

    assert "发票整理" not in repr(to_llm(session.history()))


# --- the listing -------------------------------------------------------------


def test_a_card_is_recognisable_by_how_the_conversation_opened(tmp_path):
    talk(tmp_path / "s.jsonl", "把下载目录里的发票整理一下", "再按月份分")

    assert card(tmp_path / "s.jsonl").opening == "把下载目录里的发票整理一下"


def test_a_name_is_what_a_card_shows_when_it_has_one(tmp_path):
    talk(tmp_path / "s.jsonl", "把下载目录里的发票整理一下", name="发票")

    assert card(tmp_path / "s.jsonl").title == "发票"


def test_a_card_counts_every_entry(tmp_path):
    talk(tmp_path / "s.jsonl", "一", "二")

    assert card(tmp_path / "s.jsonl").messages == 4


def test_later_turns_do_not_replace_the_opening_line(tmp_path):
    """Including the loop's own: steering and resume are turns, not openings."""
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="原来的请求"))
    session.append(UserMessage(text="等等,插一句", source="steering"))
    session.append(UserMessage(text="接着写完", source="resume"))

    assert card(tmp_path / "s.jsonl").opening == "原来的请求"


def test_a_session_opened_by_a_trigger_shows_what_triggered_it(tmp_path):
    """A cron tick is not a person, but it is still what this was about."""
    session = Session.open(tmp_path / "s.jsonl")
    session.append(UserMessage(text="每天早上整理收件箱", source="trigger:cron"))

    assert card(tmp_path / "s.jsonl").opening == "每天早上整理收件箱"


def test_a_torn_last_line_does_not_lose_the_rest(tmp_path):
    """A crash mid-write must not make the whole session unreachable."""
    talk(tmp_path / "s.jsonl", "问题")
    with (tmp_path / "s.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"kind": "user", "te')

    entry = card(tmp_path / "s.jsonl")

    assert entry.opening == "问题"
    assert entry.messages == 2, "everything before the torn line still counts"


def test_the_newest_session_is_listed_first(tmp_path):
    import os
    import time

    talk(tmp_path / "old.jsonl", "早的", name="早")
    talk(tmp_path / "new.jsonl", "晚的", name="晚")
    os.utime(tmp_path / "old.jsonl", (time.time() - 3600, time.time() - 3600))

    assert [c.name for c in catalogue(tmp_path)] == ["晚", "早"]


def test_the_listing_is_capped(tmp_path):
    for n in range(8):
        talk(tmp_path / f"s{n}.jsonl", f"第 {n} 个")

    assert len(catalogue(tmp_path, limit=3)) == 3


def test_a_directory_that_does_not_exist_is_not_an_error(tmp_path):
    assert catalogue(tmp_path / "nope") == []


def test_the_rendered_list_is_numbered_because_numbers_get_typed(tmp_path):
    talk(tmp_path / "a.jsonl", "第一个", name="甲")
    talk(tmp_path / "b.jsonl", "第二个", name="乙")

    lines = render(catalogue(tmp_path))

    assert "  1. " in lines and "  2. " in lines
    assert "甲" in lines and "乙" in lines


def test_an_empty_directory_says_so(tmp_path):
    assert "没有会话" in render(catalogue(tmp_path))


def test_an_empty_session_is_still_listed(tmp_path):
    """Otherwise a file you just made vanishes from the picker."""
    Session.open(tmp_path / "s.jsonl").path.write_text("")

    assert catalogue(tmp_path)[0].title == "(空会话)"


def test_listing_does_not_build_messages_it_will_not_use(tmp_path):
    """The reason this reads json directly: a picker must not parse everything.

    If it ever goes through Session.open, a malformed kind in one old file would
    also take the whole listing down with it - which is exactly what a picker
    exists to get you out of.
    """
    talk(tmp_path / "ok.jsonl", "好的", name="好")
    (tmp_path / "weird.jsonl").write_text(
        '{"kind": "from_a_later_version", "id": "x"}\n', encoding="utf-8"
    )

    titles = [c.title for c in catalogue(tmp_path)]

    assert "好" in titles, "one unreadable file does not hide the others"
    assert len(titles) == 2


def test_a_card_knows_which_file_it_came_from(tmp_path):
    talk(tmp_path / "s.jsonl", "问题")

    assert card(tmp_path / "s.jsonl").path == Path(tmp_path / "s.jsonl")


# --- when ---------------------------------------------------------------------


def test_how_long_ago_is_rounded_to_something_readable():
    from aven.harness.sessions import ago

    now = 1_000_000.0
    assert ago(now - 30, now=now) == "30 秒前"
    assert ago(now - 300, now=now) == "5 分钟前"
    assert ago(now - 7200, now=now) == "2 小时前"
    assert ago(now - 86400 * 3, now=now) == "3 天前"


def test_something_older_than_a_month_gets_a_date():
    """Past a month, "40 天前" stops meaning anything."""
    from aven.harness.sessions import ago

    now = 1_000_000_000.0
    assert ago(now - 86400 * 200, now=now).count("-") == 2


def test_a_clock_that_went_backwards_does_not_print_a_negative(tmp_path):
    from aven.harness.sessions import ago

    assert ago(1_000_100.0, now=1_000_000.0) == "0 秒前"


def test_the_listing_says_when_each_one_was_last_touched(tmp_path):
    talk(tmp_path / "s.jsonl", "问题", name="甲")

    assert "秒前" in render(catalogue(tmp_path))
