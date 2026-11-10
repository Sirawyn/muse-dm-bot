from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muse_bot.engine import Engine, load_config
from muse_bot.parser import parse_counts
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
THU_7PM = datetime(2026, 11, 12, 19, 0, tzinfo=TZ)   # Thursday: Public Hotel + NOFLEX
FRI_7PM = datetime(2026, 11, 13, 19, 0, tzinfo=TZ)   # Friday: NOFLEX + Nebula
MON_7PM = datetime(2026, 11, 9, 19, 0, tzinfo=TZ)    # nothing scheduled


@pytest.fixture
def eng():
    cfg = load_config("config.yaml")
    cfg["schedule"]["thursday"] = ["public_hotel", "noflex", "nebula", "marquee"]
    cfg["schedule"]["sunday"] = ["slate"]
    cfg["brand"]["ticket_link"] = "https://tix.example/muse"
    return Engine(cfg, Store(":memory:"))


def chat(eng, text, now, user="g1"):
    return eng.handle_message(user, text, now=now, username=user)


def lead(eng, user="g1"):
    return eng.store.lead(eng.store.convo(user)["lead_id"])


@pytest.mark.parametrize("text,expect", [
    ("me and 3 friends", (4, None)),
    ("4 girls 2 guys", (6, 2)),
    ("party of 5, no guys", (5, 0)),
    ("3 of us plus my boyfriend", (3, 1)),
    ("just me and my bestie", (2, None)),
    ("five girls", (5, 0)),
])
def test_counts(text, expect):
    party, guys, _ = parse_counts(text)
    assert (party, guys) == expect


def test_one_message_confirms_everything(eng):
    r = chat(eng, "hi! can 4 of us get on nebula tonight? all girls, all 21+", THU_7PM)
    assert len(r.replies) == 1
    text = r.replies[0].text
    assert text.startswith("You're all set for Nebula tonight")
    assert "Heels or leather boots are recommended (please try to avoid sneakers or Uggs)" in text
    assert "physical ID" in text and "after 11 and before 12" in text and "Devin will walk you in" in text
    assert lead(eng)["status"] == "confirmed"
    assert lead(eng)["response_seconds"] == 0


def test_asks_everything_missing_at_once_then_confirms(eng):
    r = chat(eng, "hiii can i get on the list tonight", THU_7PM)
    t = r.replies[0].text
    assert t.startswith("Hiii! We'd love to have you out")
    for q in ("which one?", "How many in your group", "Any guys", "over 21"):
        assert q in t
    chat(eng, "noflex! me and 2 girlies", THU_7PM)
    r = chat(eng, "yes", THU_7PM)          # answers the over-21 question
    assert lead(eng)["status"] == "confirmed", r.replies


def test_single_venue_night_skips_the_venue_question(eng):
    r = chat(eng, "going out sunday with 2 friends, no guys, all 21", THU_7PM)
    assert "Slate" in r.replies[0].text and lead(eng)["status"] == "confirmed"


def test_no_venue_that_night_offers_the_week(eng):
    r = chat(eng, "anything tonight?", MON_7PM)
    assert "not out tonight" in r.replies[0].text and "Thursday" in r.replies[0].text


def test_guys_get_ticket_link(eng):
    r = chat(eng, "public tonight, 3 girls 1 guy, everyone is 21+", THU_7PM)
    assert lead(eng)["status"] == "confirmed" and lead(eng)["guys"] == 1
    assert "Guys just need a ticket: https://tix.example/muse" in r.replies[0].text


def test_strict_door_asks_human_about_guys(eng):
    r = chat(eng, "marquee tonight 6 girls 1 guy all 21+", THU_7PM)
    assert "check with the door" in r.replies[0].text
    assert lead(eng)["status"] == "needs_human" and any("Needs a human" in n for n in r.notify)


def test_under_21_goes_to_human(eng):
    r = chat(eng, "noflex tonight 3 girls, I'm 21 my girl is 20 tho", THU_7PM)
    assert lead(eng)["status"] == "needs_human" and "being honest" in r.replies[0].text


def test_faq_answered_inline_not_handed_off(eng):
    r = chat(eng, "hey do we have to pay to get in? is there a table?", THU_7PM)
    t = r.replies[0].text
    assert "Girls are always free" in t and "How many in your group" in t
    assert lead(eng)["status"] == "qualifying"


def test_birthday_table_goes_to_human(eng):
    r = chat(eng, "can we get a table for my birthday saturday", THU_7PM)
    assert lead(eng)["status"] == "needs_human" and r.notify


def test_decline_gets_short_reply_and_no_questions(eng):
    r = chat(eng, "heyy not this weekend but ty!!", THU_7PM)
    assert r.replies[0].text == "No worries! We'll invite you again soon 🫶"
    assert lead(eng)["status"] == "cancelled"
    assert chat(eng, "ty!", THU_7PM).replies == []      # no greeting for a pleasantry


def test_human_takeover_silences_bot(eng):
    chat(eng, "hey", THU_7PM)
    eng.handle_human_reply("g1", "hey babe! which night?", now=THU_7PM)
    assert chat(eng, "tonight, 3 of us", THU_7PM).replies == []


def test_on_the_way_then_here(eng):
    chat(eng, "noflex tonight, 3 girls, all 21", THU_7PM)
    r = chat(eng, "we’re otw!", datetime(2026, 11, 12, 22, 50, tzinfo=TZ))
    assert "Text us when you're outside" in r.replies[0].text and lead(eng)["status"] == "arrived"
    r = chat(eng, "we’re here", datetime(2026, 11, 12, 23, 10, tzinfo=TZ))
    assert "coming out to grab you" in r.replies[0].text


def test_no_show_gets_missed_you_with_reinvite(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", THU_7PM)
    sent = eng.run_jobs(now=datetime(2026, 11, 13, 13, 5, tzinfo=TZ))
    assert lead(eng)["status"] == "no_show"
    assert "missed you" in sent[0][1].text and "tonight (NOFLEX & Nebula)" in sent[0][1].text


def test_thank_you_has_reinvite(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", THU_7PM)
    chat(eng, "we are here", datetime(2026, 11, 12, 23, 0, tzinfo=TZ))
    sent = eng.run_jobs(now=datetime(2026, 11, 13, 13, 5, tzinfo=TZ))
    assert sent[0][1].text.startswith("Thank you for coming out") and "tomorrow (NYX Karaoke" in sent[0][1].text
    assert "Thursday" not in sent[0][1].text   # only the next 3 nights, keeps it short


def test_followup_skipped_outside_24h_window(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", datetime(2026, 11, 12, 10, 0, tzinfo=TZ))
    eng.check_in(lead(eng)["id"], now=THU_7PM)
    sent = eng.run_jobs(now=datetime(2026, 11, 13, 13, 5, tzinfo=TZ))
    assert sent == [] and lead(eng)["followup_sent"] == "skipped_outside_24h"


def test_after_confirm_faq_answered_other_questions_to_team(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", THU_7PM)
    r = chat(eng, "what time should we be there?", THU_7PM)
    assert "after 11 and before 12" in r.replies[0].text and not r.notify
    r = chat(eng, "what's the address?", THU_7PM)
    assert r.notify and lead(eng)["status"] == "confirmed"


def test_new_night_after_last_one_starts_new_lead(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", THU_7PM)
    first = lead(eng)["id"]
    chat(eng, "we're here", datetime(2026, 11, 12, 23, 0, tzinfo=TZ))
    r = chat(eng, "can we come saturday too? same 3 girls", datetime(2026, 11, 13, 14, 0, tzinfo=TZ))
    assert lead(eng)["id"] != first
    assert r.replies[0].text.startswith("Hiii welcome back") and "NYX Karaoke" in r.replies[0].text


def test_weekly_ticket_links_from_muse_os_and_monday_check(eng):
    eng.cfg["brand"]["ticket_link"] = ""
    mon_9am = datetime(2026, 11, 16, 9, 0, tzinfo=TZ)
    mon_noon = datetime(2026, 11, 16, 12, 5, tzinfo=TZ)
    assert eng.weekly_ticket_check(mon_9am) == []            # too early
    alert = eng.weekly_ticket_check(mon_noon)
    assert alert and "NOFLEX" in alert[0]
    assert eng.weekly_ticket_check(mon_noon) == []           # only once per Monday
    eng.apply_venue_ticket_links({"public hotel": "https://tix.example/public-wk47"})
    r = chat(eng, "public thursday, 3 girls 1 guy, all 21+", mon_noon)
    assert "https://tix.example/public-wk47" in r.replies[0].text


@pytest.mark.parametrize("text,scout", [
    ("hiii mia sent me! can 3 of us come tonight", "Mia Lee"),
    ("my friend from @jaz.nyc told me to dm you", "Jasmine Cole"),
    ("Jasmine Cole said to message you", "Jasmine Cole"),
    ("got your page from mia", "Mia Lee"),
    ("hi can we come tonight", None),
    ("mia and i want to come out", None),          # a friend named Mia, no referral cue
])
def test_scout_referral_written_to_lead(eng, text, scout):
    eng.apply_scouts([{"name": "Mia Lee", "handle": "mia.scouts"}, {"name": "Jasmine Cole", "handle": "jaz.nyc"}])
    r = chat(eng, text, THU_7PM)
    assert lead(eng)["referred_by"] == scout
    assert any("referred by scout" in n for n in r.notify) == bool(scout)


def test_host_checkins_from_muse_os(eng):
    chat(eng, "noflex tonight, 4 girls 1 guy all 21", THU_7PM, user="a")
    chat(eng, "noflex tonight, 2 girls all 21", THU_7PM, user="b")
    a, b = lead(eng, "a")["id"], lead(eng, "b")["id"]
    entries = [{"lead_id": f"DM-{a:05d}", "status": "Arrived", "girls_arrived": 3},
               {"lead_id": f"DM-{b:05d}", "status": "No-show", "girls_arrived": 0},
               {"lead_id": "OTHER-1", "status": "Arrived", "girls_arrived": 9}]
    assert eng.apply_checkins(entries) == 2
    assert eng.apply_checkins(entries) == 0                    # repeats are harmless
    assert (lead(eng, "a")["status"], lead(eng, "a")["girls_arrived"], lead(eng, "a")["arrived_count"]) == ("arrived", 3, 4)
    assert lead(eng, "b")["status"] == "no_show"
    eng.apply_checkins([{"lead_id": f"DM-{b:05d}", "status": "Confirmed", "girls_arrived": None}])   # host undo
    assert lead(eng, "b")["status"] == "confirmed"
    sent = dict(eng.run_jobs(now=datetime(2026, 11, 13, 13, 5, tzinfo=TZ)))
    assert sent["a"].text.startswith("Thank you") and "missed you" in sent["b"].text


def test_lineup_host_from_muse_os_used_in_messages(eng):
    eng.apply_night_hosts({"noflex": {"2026-11-12": "Mia", "2026-11-13": None}})
    r = chat(eng, "noflex tonight, 3 girls all 21", THU_7PM)
    assert "Mia will walk you in" in r.replies[0].text and "Host: Mia" in r.notify[0]
    r = chat(eng, "we're outside", datetime(2026, 11, 12, 23, 0, tzinfo=TZ))
    assert r.replies[0].text.startswith("Yay! Mia is coming out")
    r = chat(eng, "noflex tomorrow, 2 girls all 21", THU_7PM, user="z")   # nobody on lineup -> config host
    assert "Devin will walk you in" in r.replies[0].text


def test_dated_lineup_overrides_weekly_pattern(eng):
    # This Friday only Petite Disco is on the lineup (not the usual NOFLEX & Nebula)
    eng.apply_night_hosts({"petite disco": {"2026-11-13": "Mia"}, "noflex": {"2026-11-12": None, "2026-11-19": None}})
    r = chat(eng, "can 2 of us come friday? all girls all 21", THU_7PM)
    assert "Petite Disco" in r.replies[0].text and "Mia will walk you in" in r.replies[0].text
    r = chat(eng, "anything saturday?", THU_7PM, user="z")    # not on the lineup -> not offered
    assert "not out Saturday" in r.replies[0].text


def test_after_midnight_counts_as_friday_and_says_head_over_now(eng):
    sat_0040 = datetime(2026, 11, 14, 0, 40, tzinfo=TZ)            # still Friday night
    r = chat(eng, "hii can me and 2 girls get on nebula tonight? all 21", sat_0040)
    t = r.replies[0].text
    assert lead(eng)["night"] == "2026-11-13" and "You're all set for Nebula tonight" not in t
    # 12:40am is past the 12:30 last entry: a person checks the door instead
    assert "check if we can still get you in" in t and lead(eng)["status"] == "needs_human"


def test_late_request_before_last_entry(eng):
    fri_2350 = datetime(2026, 11, 13, 23, 50, tzinfo=TZ)
    r = chat(eng, "nebula tonight, 3 girls all 21", fri_2350)
    assert "head over now, the door closes at 12:30am" in r.replies[0].text
    assert r.notify[0].startswith("⏰ Late add")


def test_nightly_digest_per_venue_and_host(eng):
    eng.apply_night_hosts({"nebula": {"2026-11-13": "Mia"}, "noflex": {"2026-11-13": "Sam", "2026-11-20": None}})
    chat(eng, "nebula friday, 3 girls all 21", THU_7PM, user="a")
    chat(eng, "noflex friday, 2 girls 1 guy all 21", THU_7PM, user="b")
    assert eng.nightly_digest(datetime(2026, 11, 13, 20, 0, tzinfo=TZ)) == []        # before 9pm
    d = eng.nightly_digest(datetime(2026, 11, 13, 21, 1, tzinfo=TZ))
    assert any("Nebula (host Mia): 1 groups, 3 girls, 0 guys. @a (3)" in x for x in d)
    assert any("NOFLEX (host Sam): 1 groups, 2 girls, 1 guys" in x for x in d)
    assert eng.nightly_digest(datetime(2026, 11, 13, 21, 6, tzinfo=TZ)) == []        # once a night
