"""Guests who say thank you get their message liked (a heart), on top of any normal reply."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

FRI_7PM = datetime(2026, 11, 13, 19, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, user="g1"):
    return eng.handle_message(user, text, now=FRI_7PM, username=user)


@pytest.mark.parametrize("text", ["thank you!!", "Thank u", "ty", "tyy", "tysm 💕", "thanks!", "thx",
                                  "oh ok thank u! but I think it's just gna be girls", "appreciate it"])
def test_thanks_gets_a_like(eng, text):
    chat(eng, "can 4 of us come to nebula tonight? all girls, all 21+")
    assert chat(eng, text).react


def test_thanks_while_qualifying_still_answers(eng):
    chat(eng, "can we come to nebula tonight")
    r = chat(eng, "4 of us, thank you!")
    assert r.react and r.replies


def test_thanks_with_no_live_booking(eng):
    r = chat(eng, "thank you!")
    assert r.react and not r.replies


@pytest.mark.parametrize("text", ["hi can 3 of us come tonight", "ok", "sounds good", "they", "the 21st"])
def test_no_like_without_thanks(eng, text):
    assert not chat(eng, text).react
