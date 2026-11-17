"""The bot is Thalia, Muse's assistant. It says it's a bot up front and never passes as a person on the team."""
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


@pytest.mark.parametrize("first", ["hi can i get on the list tonight", "is there a dress code?",
                                   "can 4 of us come to nebula tonight", "hii can i get a table at nebula for friday"])
def test_first_reply_says_thalia_is_a_bot(eng, first):
    t = chat(eng, first).replies[0].text
    assert "Thalia" in t and "Muse's assistant" in t


@pytest.mark.parametrize("q", ["are you a bot?", "is this a real person?", "r u a bot lol", "am i talking to a bot"])
def test_asked_if_bot_says_yes(eng, q):
    chat(eng, "can 4 of us come to nebula tonight")
    t = " ".join(o.text for o in chat(eng, q).replies)
    assert "I'm Thalia, Muse's assistant" in t


def test_handoff_says_a_real_person_will_reply(eng):
    r = chat(eng, "can i book a table for my bachelorette")
    assert "real person" in r.replies[0].text
    assert any("start with your first name" in n for n in r.notify)


def test_no_message_template_claims_to_be_staff(eng):
    import re
    for key, text in eng.msgs.items():
        assert not re.search(r"\b(this is|it's|i'm|im) (devin|evelyn|audrey|matthew|terek)\b", str(text).lower()), key


def test_no_emojis_and_no_word_bot_in_anything_a_guest_sees(eng):
    import re
    emoji = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2764\uFE0F]")
    guest_facing = {**{f"messages.{k}": v for k, v in eng.msgs.items()},
                    **{f"faq.{k}": v for k, v in eng.cfg.get("faq", {}).items()}}
    for key, text in guest_facing.items():
        assert not emoji.search(str(text)), key
        assert not re.search(r"\bbots?\b", str(text), re.I), key
