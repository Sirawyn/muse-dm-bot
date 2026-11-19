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


def _booked_then_team_promises(eng):
    for t in ("noflex friday", "8", "no guys, just girls", "yes"):
        chat(eng, t, datetime(2026, 11, 17, 16, 5, tzinfo=TZ))
    eng.handle_human_reply("g1", "Let me ask the manager for you!", now=datetime(2026, 11, 17, 16, 18, tzinfo=TZ))


def test_thalia_never_takes_back_a_chat_the_team_promised_to_follow_up(eng):
    # Father: "make sure not to respond to her until I get back to her"
    _booked_then_team_promises(eng)
    for t in ("ok thank you!", "also what time should we get there friday?", "is the list still good?"):
        r = chat(eng, t, datetime(2026, 11, 17, 17, 0, tzinfo=TZ))
        assert r.replies == [] and r.notify


def test_team_reply_or_resume_ends_the_hold(eng):
    _booked_then_team_promises(eng)
    eng.resume_bot("g1")
    r = chat(eng, "what time should we get there friday?", datetime(2026, 11, 17, 17, 0, tzinfo=TZ))
    assert r.replies


@pytest.mark.parametrize("text", ["I see thank you!", "oh ok thanks so much", "got it thank u", "Thank you!"])
def test_thank_you_alone_never_starts_a_new_chat(eng, text):
    # Allison, Nov 18: "I see thank you!" got "Hiii! I'm Thalia..." as if she were new
    r = chat(eng, text, datetime(2026, 11, 18, 11, 56, tzinfo=TZ))
    assert r.replies == [] and r.react
