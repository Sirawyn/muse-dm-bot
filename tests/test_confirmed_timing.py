"""Regression (Nov 27 2026): a booked guest asked "What time can we come in tn?" and the reply said
"text us when you're outside" twice."""
from datetime import datetime
from zoneinfo import ZoneInfo

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

FRI_930PM = datetime(2026, 11, 27, 21, 30, tzinfo=ZoneInfo("America/New_York"))


def test_timing_question_after_booking_says_outside_once():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    eng.handle_message("g", "noflex tonight 3 girls all 21", now=FRI_930PM, username="g")
    t = eng.handle_message("g", "Hi! What time can we come in tn?", now=FRI_930PM, username="g").replies[0].text
    assert t.lower().count("outside") == 1
    assert "You're already on the list for NOFLEX tonight!" in t
