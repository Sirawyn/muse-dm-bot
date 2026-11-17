"""Regression (Allison, Nov 17 2026): the team sent a personal apology at 12:53am. She replied at 12:19pm with a
question about music, and Thalia jumped in with "welcome back! ... which night works for you?", ignoring both
the team's message and her question."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, now, user="g1"):
    return eng.handle_message(user, text, now=now, username=user)


def test_reply_to_a_team_message_hours_later_goes_to_the_team(eng):
    chat(eng, "hi", datetime(2026, 11, 13, 17, 14, tzinfo=TZ))
    eng.handle_human_reply("g1", "Hi Allison, we are deeply sorry for the poor response...",
                           now=datetime(2026, 11, 17, 0, 53, tzinfo=TZ))
    r = chat(eng, "Hi that's okay! We're still interested! What type of music is normally played at NOFLEX?",
             datetime(2026, 11, 17, 12, 19, tzinfo=TZ))
    assert r.replies == []
    assert any("replied to the team" in n for n in r.notify)


def test_double_text_with_no_team_reply_lets_thalia_help(eng):
    chat(eng, "hi", datetime(2026, 11, 13, 17, 14, tzinfo=TZ))
    eng.handle_human_reply("g1", "we're at NOFLEX friday if you want to come", now=datetime(2026, 11, 17, 0, 53, tzinfo=TZ))
    assert chat(eng, "yes!", datetime(2026, 11, 17, 12, 19, tzinfo=TZ)).replies == []
    r = chat(eng, "friday, 8 of us", datetime(2026, 11, 17, 15, 0, tzinfo=TZ))   # nobody answered: Thalia helps
    assert r.replies


@pytest.mark.parametrize("q", ["What type of music is normally played at NOFLEX?",
                               "Hi! what kind of music do they play at nebula friday?",
                               "where do we park?"])
def test_question_thalia_cant_answer_is_never_skipped(eng, q):
    r = chat(eng, q, datetime(2026, 11, 17, 12, 19, tzinfo=TZ))
    assert r.replies == [] and any("can't answer" in n for n in r.notify)
