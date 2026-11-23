"""Regression (Nov 23 2026): "anything for thanksgiving Eve" got "What night are you thinking?", and
"How's it looking for 3 girls and 1 guy" got silence."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import normalize, parse_night
from muse_bot.store import Store

MON = datetime(2026, 11, 23, 16, 52, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize("text,d", [("thanksgiving Eve", date(2026, 11, 25)), ("thanksgiving", date(2026, 11, 26)),
                                    ("black friday", date(2026, 11, 27)), ("nye", date(2026, 12, 31)),
                                    ("new years eve", date(2026, 12, 31)), ("christmas eve", date(2026, 12, 24)),
                                    ("halloween", date(2027, 10, 31))])
def test_holidays_are_dates(text, d):
    assert parse_night(normalize(text), MON) == d


def test_real_conversation_thanksgiving_eve():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    eng.cfg["schedule"]["wednesday"] = ["nebula"]
    r = eng.handle_message("t", "hey! Are you guys promoting anything for thanksgiving Eve", now=MON, username="t")
    assert "What night" not in r.replies[0].text
    assert eng.store.convo("t")["draft"]["night"] == "2026-11-25"
    r = eng.handle_message("t", "How’s it looking for 3 girls and 1 guy", now=MON, username="t")
    assert r.replies   # a booking question, never silence
    d = eng.store.convo("t")["draft"]
    assert (d["party_size"], d["guys"]) == (4, 1)
