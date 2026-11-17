"""NOFLEX is cover only: guys never need a ticket there. The bot must never tell a NOFLEX guest to buy one."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.store import Store

THU_3PM = datetime(2026, 11, 19, 15, 0, tzinfo=ZoneInfo("America/New_York"))   # Thursday: Public Hotel + NOFLEX


@pytest.fixture
def eng():
    return Engine(load_config("config.yaml"), Store(":memory:"))


def chat(eng, text, user="g1"):
    return eng.handle_message(user, text, now=THU_3PM, username=user)


def texts(r):
    return " ".join(o.text for o in r.replies)


def test_noflex_is_cover_in_config(eng):
    assert eng.policy_for("noflex", "2026-11-19") == "cover"


@pytest.mark.parametrize("first", ["can my guy friend come", "how much for guys?", "can my guy friend come tonight"])
def test_guys_question_before_venue_names_each_rule(eng, first):
    t = texts(chat(eng, first))
    assert "At NOFLEX they just pay cover at the door, no ticket" in t
    assert "they need a ticket" in t and "Guys can come, they just need a ticket" not in t


def test_guys_question_about_noflex(eng):
    t = texts(chat(eng, "do guys need tickets for noflex?"))
    assert "cover at the door" in t and "need a ticket" not in t.replace("don't need a ticket", "")


@pytest.mark.parametrize("flow", [
    ["can 3 of us come to noflex tonight", "yes 2 guys", "yes"],
    ["can 3 of us come to noflex tonight", "not sure", "yes"],
    ["noflex tonight", "3 of us", "no guys", "yes", "actually 2 guys are coming"],
])
def test_noflex_never_asks_for_a_ticket(eng, flow):
    said = []
    for m in flow:
        r = chat(eng, m)
        said += [o.text for o in r.replies]
        assert not any("ticket link" in n for n in r.notify)
    t = " ".join(said).replace("don't need a ticket", "").replace("no ticket", "")
    assert "ticket" not in t
    assert "cover at the door" in " ".join(said)


def test_guys_added_after_confirm_get_an_answer(eng):
    for m in ("noflex tonight", "3 of us", "no guys", "yes"):
        chat(eng, m)
    r = chat(eng, "actually 2 guys are coming")
    assert r.replies and "cover at the door" in r.replies[0].text
    assert any("2 guys" in n for n in r.notify)
