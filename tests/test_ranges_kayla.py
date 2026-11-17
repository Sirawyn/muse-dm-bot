"""Regression (Kayla, Nov 17 2026): "5-6" to "How many of you are coming?" was read as 56, so Thalia said
"Love a big group!" and handed off a normal group of six."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import parse_counts
from muse_bot.store import Store

TUE_5PM = datetime(2026, 11, 17, 17, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize("text,size", [("5-6", 6), ("5 - 6", 6), ("5 to 6", 6), ("5 or 6", 6), ("3-4", 4), ("7–8", 8)])
def test_headcount_range_takes_the_bigger_number(text, size):
    assert parse_counts(text, "party_size")[0] == size


def test_real_conversation_kayla():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    chat = lambda t: eng.handle_message("k", t, now=TUE_5PM, username="k")
    chat("Hello can I get a tablet no flex on Saturday")
    chat("Yes! Nebula sounds great")
    r = chat("5-6")
    assert "big group" not in r.replies[0].text and "guys" in r.replies[0].text.lower()
    chat("All girls")
    r = chat("yes")
    assert r.replies[0].text.startswith("You're all set for Nebula Saturday")   # Saturday, as she asked
    lead = eng.store.lead(eng.store.convo("k")["lead_id"])
    assert (lead["party_size"], lead["night"]) == (6, "2026-11-21")
