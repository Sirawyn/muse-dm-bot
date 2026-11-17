"""Regression: a guest answered "not sure" to "Any guys coming with you?" and the bot asked the same
question again (Nov 2026). The bot must never repeat a question she just answered."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
FRI_7PM = datetime(2026, 11, 13, 19, 0, tzinfo=TZ)
START = "can 4 of us come to nebula tonight"


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, user="g1"):
    return eng.handle_message(user, text, now=FRI_7PM, username=user)


def lead(eng, user="g1"):
    return eng.store.lead(eng.store.convo(user)["lead_id"])


@pytest.mark.parametrize("answer", ["not sure", "Not sure!", "idk", "idk yet", "no idea", "maybe", "not sure yet lol",
                                    "im not sure how many guys", "we'll see", "tbd"])
def test_unsure_about_guys_moves_on(eng, answer):
    assert "Any guys" in chat(eng, START).replies[0].text
    r = chat(eng, answer)
    assert len(r.replies) == 2
    info, ask = r.replies[0].text, r.replies[1].text
    assert "Any guys" not in info + ask and "How many guys" not in info + ask   # never the same question again
    assert info.startswith("Ok no worries! Just let us know soon!")
    assert "ticket" in info and "paying at the door" in info and "one guy at the table" in info
    assert ask == "Is everyone 21+? They only take physical IDs at the door"
    r = chat(eng, "yes")
    assert r.replies[0].text.startswith("You're all set for Nebula tonight")
    assert any("guys TBD" in n for n in r.notify)
    assert lead(eng)["status"] == "confirmed"


def test_unsure_after_how_many_guys(eng):
    chat(eng, START)
    assert chat(eng, "yes some guys").replies[0].text == "How many guys?"
    r = chat(eng, "not sure yet")
    t = " ".join(o.text for o in r.replies)
    assert "How many guys" not in t and "Any guys" not in t and "21+" in t


@pytest.mark.parametrize("answer,guys", [("maybe 2", 2), ("probably 3", 3), ("like 2 guys", 2), ("2", 2)])
def test_hedged_guy_counts(eng, answer, guys):
    chat(eng, START)
    chat(eng, answer)
    chat(eng, "yes")
    assert lead(eng)["guys"] == guys


def test_plain_no_is_still_no_guys(eng):
    chat(eng, START)
    chat(eng, "no")
    r = chat(eng, "yes")
    assert "no worries" not in r.replies[0].text.lower()
    assert lead(eng)["guys"] == 0


def test_never_sends_the_same_message_twice(eng):
    chat(eng, START)
    r = chat(eng, "purple elephant")       # nonsense to the guys question
    last = chat(eng, "purple elephant")
    texts = [o.text for o in r.replies + last.replies]
    assert len(texts) == len(set(texts))


def test_real_conversation_rose(eng):
    """The team's own handling (Nebula, Friday): not sure -> no worries + guys info -> 'just girls' -> Sounds good."""
    chat(eng, "hii can i get a table at nebula for friday")
    chat(eng, "we're expecting about 5-6 people!")
    r = chat(eng, "not sure yet")
    assert r.replies[0].text.startswith("Ok no worries! Just let us know soon")
    r = chat(eng, "yes")
    assert r.replies[0].text.startswith("You're all set for Nebula")
    r = chat(eng, "oh ok thank u! but I think it's just gna be girls")
    assert [o.text for o in r.replies] == ["Sounds good!"]
    assert lead(eng)["guys"] == 0


def test_guys_told_after_confirm_get_ticket_info(eng):
    chat(eng, START)
    chat(eng, "not sure")
    chat(eng, "yes")
    r = chat(eng, "2 guys are coming")
    assert r.replies[0].text.startswith("Got it!") and "ticket" in r.replies[0].text
    assert lead(eng)["guys"] == 2
    assert any("2 guys" in n for n in r.notify)
