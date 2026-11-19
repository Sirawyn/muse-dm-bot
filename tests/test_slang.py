"""Texting shorthand from the real Muse DM history must read the same as the long form."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import normalize, parse, parse_night
from muse_bot.store import Store

TUE_3PM = datetime(2026, 11, 17, 15, 0, tzinfo=ZoneInfo("America/New_York"))
VENUES = load_config("config.yaml")["venues"]


@pytest.mark.parametrize("short,long", [
    ("abt 5 of us", "about 5 of us"), ("r u there rn", "are you there rn"), ("lmk pls", "let me know please"),
    ("ur list", "your list"), ("cuz we r 21", "because we are 21"), ("gna be 4 ppl", "going to be 4 people"),
    ("def coming tmrw", "definitely coming tmrw"), ("wanna come thurs", "want to come thurs"),
])
def test_shorthand_expands(short, long):
    assert normalize(short) == normalize(long)


@pytest.mark.parametrize("text,night", [("do u have anything for tnit?", date(2026, 11, 17)),
                                        ("2nite?", date(2026, 11, 17)), ("tomo night", date(2026, 11, 18)),
                                        ("im away atm", None)])
def test_shorthand_nights(text, night):
    assert parse_night(normalize(text), TUE_3PM) == night


def test_real_guest_phrasings():
    assert parse("actually my friends r 21", TUE_3PM, VENUES).over21 is True
    assert parse("abt 6 of us", TUE_3PM, VENUES, "party_size").party_size == 6
    assert parse("nw", TUE_3PM, VENUES).pleasantry


def test_full_chat_in_shorthand():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    say = lambda t: eng.handle_message("s", t, now=TUE_3PM, username="s").replies
    say("hii r u guys doing nebula fri? abt 5 of us")
    say("no guys js girls")
    r = say("yes we r all 21")
    assert r and r[0].text.startswith("You're all set for Nebula Friday")
