"""Regression (Nov 2026): a guest asked about Marquee (not running), then at 2:47am Saturday wrote
"Can I go to nebula or marquee now are u there" and got no reply."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import normalize, parse_night
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, now, user="g1"):
    return eng.handle_message(user, text, now=now, username=user)


def test_real_conversation_iris(eng):
    chat(eng, "hi", datetime(2026, 11, 11, 8, 11, tzinfo=TZ))
    chat(eng, "Tomorrow", datetime(2026, 11, 11, 8, 12, tzinfo=TZ))
    t = chat(eng, "do u have marquee", datetime(2026, 11, 11, 8, 13, tzinfo=TZ)).replies[0].text
    assert t.startswith("We're not hosting at Marquee right now.")
    r = chat(eng, "Can I go to nebula or marquee now are u there", datetime(2026, 11, 14, 2, 47, tzinfo=TZ))
    t = r.replies[0].text
    assert "The list for Nebula tonight closed at 1:30am" in t   # Nebula is running; Marquee isn't
    assert "tomorrow" not in t.lower() and "Saturday" in t       # 2:47am: "tomorrow" would read as Sunday


@pytest.mark.parametrize("text,expect", [("can we come now", True), ("are u there rn", True),
                                         ("not going out rn", False), ("ok for now", False)])
def test_now_means_tonight(text, expect):
    now = datetime(2026, 11, 13, 22, 0, tzinfo=TZ)
    assert (parse_night(normalize(text), now) is not None) == expect


def test_two_venues_takes_the_one_running(eng):
    r = chat(eng, "can we go to marquee or nebula tonight", datetime(2026, 11, 13, 20, 0, tzinfo=TZ))
    assert "Marquee" not in r.replies[0].text
    assert eng.store.convo("g1")["draft"]["venue"] == "nebula"
