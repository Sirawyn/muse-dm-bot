"""Real (anonymized) guest messages from the Muse Instagram DM history: tests/data/parser_cases.json."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import load_config
from muse_bot.parser import parse, parse_counts

CASES = json.loads((Path(__file__).parent / "data" / "parser_cases.json").read_text())
VENUES = load_config(str(Path(__file__).parent.parent / "config.yaml"))["venues"]
NOW = datetime(2026, 11, 12, 19, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize("text,party,guys", CASES["party_size"])
def test_party_size_and_guys(text, party, guys):
    got_party, got_guys, _ = parse_counts(text)
    assert (got_party, got_guys) == (party, guys)


@pytest.mark.parametrize("text,arrived", CASES["arrival"])
def test_arrival(text, arrived):
    assert parse(text, NOW, VENUES).arrived is arrived


@pytest.mark.parametrize("text", CASES["should_NOT_hand_off (answer from FAQ instead)"])
def test_faq_answered_not_handed_off(text):
    p = parse(text, NOW, VENUES)
    assert not p.wants_human and p.faq


@pytest.mark.parametrize("text", CASES["should_hand_off"])
def test_hand_off(text):
    assert parse(text, NOW, VENUES).wants_human


@pytest.mark.parametrize("text", [
    "heyy not this weekend but ty!!", "Sorry, can’t this Saturday", "maybe next weekend when it's warmer 😛",
    "I can’t make it this time but I’d love to go in the future if possible!",
    "Hiii all my friends are out of town this week 🥲", "unfortunately i won’t be able to this week",
    "Hey thanks for asking but i have plans for this wknd alr.",
])
def test_decline(text):
    assert parse(text, NOW, VENUES).declined


@pytest.mark.parametrize("text,ok", [
    ("Ok great! Everyone is 21+", True), ("yes we are all 21 and girls :)", True), ("5 girls all 21+", True),
    ("wait also a diff girl is under 21", False), ("I'm 21 my girl is 20 tho", False),
    ("We are at another party rn so we don’t have physical id", False),
    ("They wont let me in because I forgot my id but i have a passport photo", False),
    ("2 girls 21+ and 1 guy not 21 yet", False),
])
def test_age(text, ok):
    assert parse(text, NOW, VENUES).over21 is ok
