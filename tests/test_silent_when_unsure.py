"""Father's litmus: if Thalia doesn't know how to respond, she stays silent (and alerts the team)
instead of saying the wrong thing."""
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


@pytest.mark.parametrize("q", ["Is this the right location?", "Is it asians only?", "do you guys have parking?"])
def test_question_she_cant_answer_gets_silence_and_an_alert(eng, q):
    r = chat(eng, q)
    assert r.replies == []
    assert any("Needs a human" in n for n in r.notify)


def test_unreadable_answer_mid_chat_gets_silence(eng):
    chat(eng, "can 4 of us come to nebula tonight")
    r = chat(eng, "purple elephant")
    assert r.replies == [] and r.notify


def test_unknown_question_after_confirming_gets_silence(eng):
    chat(eng, "can 4 of us come to nebula tonight? all girls, all 21+")
    r = chat(eng, "is there parking nearby?")
    assert r.replies == [] and r.notify


def test_known_handoffs_still_tell_her_a_person_will_reply(eng):
    r = chat(eng, "its my bday saturday can we get a sign")   # Thalia knows this needs the team
    assert r.replies and "real person" in r.replies[0].text
