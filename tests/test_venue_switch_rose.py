"""Regression (Rose, Nov 17 2026): "can I get a table at noflex for friday instead of nebula?" got an answer
about Nebula, because both venues were named and "instead of" was ignored."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import normalize, parse_venues
from muse_bot.store import Store

TUE_6PM = datetime(2026, 11, 17, 18, 43, tzinfo=ZoneInfo("America/New_York"))
VENUES = load_config("config.yaml")["venues"]


@pytest.mark.parametrize("text,first", [
    ("Actually can I get a table at noflex for friday instead of nebula? sorry for the inconvenience", "noflex"),
    ("nebula instead of noflex", "nebula"),
    ("switch from nebula to noflex", "noflex"),
    ("not nebula, noflex pls", "noflex"),
])
def test_the_venue_she_wants_comes_first(text, first):
    assert parse_venues(normalize(text), VENUES)[0] == first


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, user="r"):
    return eng.handle_message(user, text, now=TUE_6PM, username=user)


def test_switch_while_booking(eng):
    chat(eng, "hii can i get a table at nebula for friday")
    chat(eng, "we're expecting about 5-6 people!")
    chat(eng, "no guys")
    r = chat(eng, "Actually can I get a table at noflex for friday instead of nebula? sorry for the inconvenience")
    t = " ".join(o.text for o in r.replies)
    assert "Nebula" not in t
    assert eng.store.convo("r")["draft"]["venue"] == "noflex"


def test_switch_after_confirming_rebooks_and_cancels_old(eng):
    chat(eng, "can 6 of us come to nebula friday? all girls, all 21+")
    old = eng.store.lead(eng.store.convo("r")["lead_id"])
    assert old["status"] == "confirmed"
    r = chat(eng, "Actually can I get a table at noflex for friday instead of nebula?")
    assert eng.store.lead(old["id"])["status"] == "cancelled"
    assert "You're all set for NOFLEX Friday" in r.replies[0].text and "?" not in r.replies[0].text   # nothing asked twice
    new = eng.store.lead(eng.store.convo("r")["lead_id"])
    assert (new["venue"], new["night"], new["party_size"]) == ("noflex", "2026-11-20", 6)
    assert any("moved from Nebula to NOFLEX" in n for n in r.notify)
