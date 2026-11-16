"""Regression: guests asking for a night more than a week out, or by calendar date.

Real case (Nov 13, 2026, a Friday): a guest asked for "Nov 20th" for 7-8 girls, then said "next Friday".
The bot read "next Friday" as tonight, booked her for NOFLEX that night, told her she was "already on the
list for tonight" when she repeated "November 20th", and sent "we missed you" the next day.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import parse_night, normalize
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
FRI_514PM = datetime(2026, 11, 13, 17, 14, tzinfo=TZ)
TUE_7PM = datetime(2026, 11, 10, 19, 0, tzinfo=TZ)


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, now, user="g1"):
    return eng.handle_message(user, text, now=now, username=user)


def lead(eng, user="g1"):
    return eng.store.lead(eng.store.convo(user)["lead_id"])


@pytest.mark.parametrize("text,now,expect", [
    ("next friday", FRI_514PM, date(2026, 11, 20)),
    ("We were thinking next Friday!!", FRI_514PM, date(2026, 11, 20)),
    ("friday next week", TUE_7PM, date(2026, 11, 20)),
    ("next friday", TUE_7PM, date(2026, 11, 20)),
    ("this friday", TUE_7PM, date(2026, 11, 13)),
    ("friday", FRI_514PM, date(2026, 11, 13)),
    ("Next Friday! November 20th!", FRI_514PM, date(2026, 11, 20)),
    ("going out on Nov 20th!", FRI_514PM, date(2026, 11, 20)),
    ("nov. 20", FRI_514PM, date(2026, 11, 20)),
    ("11/20", FRI_514PM, date(2026, 11, 20)),
    ("Saturday the 21st", FRI_514PM, date(2026, 11, 21)),
    ("the 21st", FRI_514PM, date(2026, 11, 21)),
    ("jan 2", FRI_514PM, date(2027, 1, 2)),
    ("tonight", FRI_514PM, date(2026, 11, 13)),
])
def test_parse_night_dates(text, now, expect):
    assert parse_night(normalize(text), now) == expect


@pytest.mark.parametrize("text", ["I'm 21", "we have 7-8 girls", "everyone is 21+", "may we bring 3 friends",
                                  "get there at 11", "24/7 party girls"])
def test_numbers_are_not_dates(text):
    assert parse_night(normalize(text), FRI_514PM) is None


def test_real_conversation_nov_20(eng):
    r = chat(eng, "Hiii! I hope you're well! We're interested in going out on Nov 20th! It'll be a group of about "
                  "7-8 girls and we'd love to know what's happening around the city on that night!", FRI_514PM)
    t = r.replies[0].text
    assert "tonight" not in t.lower()
    assert "Friday Nov 20" in t
    assert lead(eng)["night"] == "2026-11-20"

    r = chat(eng, "Hi! We were thinking next Friday!!", FRI_514PM)
    assert lead(eng)["night"] == "2026-11-20"
    assert all("tonight" not in o.text.lower() for o in r.replies)


def test_change_of_night_after_confirm_cancels_old_booking(eng):
    chat(eng, "can 4 of us come to nebula tonight? all girls, all 21+", FRI_514PM)
    old = lead(eng)
    assert old["status"] == "confirmed" and old["night"] == "2026-11-13"

    r = chat(eng, "Next Friday! November 20th!", FRI_514PM)
    assert not any("already on the list" in o.text for o in r.replies)
    assert eng.store.lead(old["id"])["status"] == "cancelled"   # no "we missed you" tomorrow
    assert lead(eng)["night"] == "2026-11-20"
    assert lead(eng)["id"] != old["id"]
    assert any("moved" in n.lower() for n in r.notify)
