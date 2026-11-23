"""Father's rule (Nov 23 2026): if this week's schedule isn't uploaded to Muse OS yet, Thalia never answers.
A guest asked on Monday "where will yall be on Wednesday night??" and got last week's lineup."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
MON_531PM = datetime(2026, 11, 23, 17, 31, tzinfo=TZ)


@pytest.fixture
def eng():
    e = Engine(load_config("config.yaml"), Store(":memory:"))
    e.require_lineup = True   # Muse OS connected
    # last week's lineup only (Nov 19-21)
    e.apply_night_hosts({"noflex": {"2026-11-19": "Devin", "2026-11-20": None},
                         "nebula": {"2026-11-20": None, "2026-11-21": None}})
    return e


def chat(eng, text, now=MON_531PM, user="w"):
    return eng.handle_message(user, text, now=now, username=user)


def test_no_reply_until_this_weeks_lineup_is_uploaded(eng):
    r = chat(eng, "Hi where will yall be on Wednesday night??")
    assert r.replies == []
    assert any("schedule isn't in Muse OS yet" in n for n in r.notify)
    assert chat(eng, "hello?").notify == []          # one alert per guest per day, not one per message


def test_replies_once_the_week_is_uploaded(eng):
    chat(eng, "Hi where will yall be on Wednesday night??")
    eng.apply_night_hosts({"noflex": {"2026-11-26": None}, "nebula": {"2026-11-27": None, "2026-11-28": None}})
    r = chat(eng, "what about friday?")
    assert r.replies   # Friday now has a lineup (Nebula only): she carries on with the booking


def test_monday_alert_reminds_the_team(eng):
    out = eng.weekly_ticket_check(now=datetime(2026, 11, 23, 12, 5, tzinfo=TZ))
    assert any("schedule isn't in Muse OS yet" in o for o in out)


def test_without_muse_os_the_config_schedule_still_works():
    e = Engine(load_config("config.yaml"), Store(":memory:"))
    assert e.handle_message("x", "where will yall be on Wednesday night??", now=MON_531PM, username="x").replies
