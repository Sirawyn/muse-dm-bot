"""Regressions from Nov 25-26 2026.
May: "hope you had an amazing Thanksgiving! ... go out tomorrow night (11/27) ... any bottle/table services"
got tonight's venues and "you get a table with drinks". Crystal: "3 girls and 1 guy ... nebula tonight", then
"yesyes" to the 21+ question, and Thalia went silent."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import normalize, parse, parse_counts, parse_night
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
THU_1016PM = datetime(2026, 11, 26, 22, 16, tzinfo=TZ)
WED_1118PM = datetime(2026, 11, 25, 23, 18, tzinfo=TZ)
MAY = ("Hiii I hope your team has had an amazing Thanksgiving! A group of my girl friends were looking to go out "
       "tomorrow night (11/27) and we were wondering if you had any bottle/table services available👀")
VENUES = load_config("config.yaml")["venues"]


def test_explicit_night_beats_a_holiday_mentioned_in_passing():
    assert parse_night(normalize(MAY), THU_1016PM) == date(2026, 11, 27)
    assert parse_night(normalize("anything for thanksgiving eve?"), datetime(2026, 11, 23, 12, tzinfo=TZ)) == date(2026, 11, 25)


@pytest.mark.parametrize("text", [MAY, "do you do bottle service", "any table service friday?"])
def test_buying_bottles_or_tables_goes_to_the_team(text):
    assert parse(text, THU_1016PM, VENUES).wants_human


@pytest.mark.parametrize("text", ["yesyes", "yess", "yea yea", "we all do", "we all are", "all of us",
                                  "everyone is", "yep all good"])
def test_yes_variants_answer_the_age_question(text):
    assert parse(text, WED_1118PM, VENUES, "age").over21 is True


def test_group_of_girls_and_a_guy_counts_everyone():
    assert parse_counts("I have a group of 3 girls and 1 guy (including myself)")[:2] == (4, 1)


def test_real_conversation_crystal():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    eng.cfg["schedule"]["wednesday"] = ["nebula"]
    say = lambda t: eng.handle_message("c", t, now=WED_1118PM, username="c")
    say("heyyyyy I have a group of 3 girls and 1 guy (including myself) and was wondering if it would be possible "
        "to still get guest list or faster entry for nebula tonight?")
    r = say("yesyes")
    assert r.replies and r.replies[0].text.startswith("You're all set for Nebula tonight")
    lead = eng.store.lead(eng.store.convo("c")["lead_id"])
    assert (lead["party_size"], lead["guys"]) == (4, 1)
