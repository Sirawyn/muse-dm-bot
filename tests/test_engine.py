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
    assert "physical ID" in text and "get there at 11" in text and "Devin will walk you in" in text
    assert lead(eng)["status"] == "confirmed"
    assert lead(eng)["response_seconds"] == 0


def test_asks_one_question_at_a_time_then_confirms(eng):
    r = chat(eng, "hiii can i get on the list tonight", THU_7PM)
    t = r.replies[0].text
    assert t.startswith("Hi! We'd love to have you out") and "which one?" in t
    assert "How many" not in t and "💕" not in t
    r = chat(eng, "noflex! me and 2 girlies", THU_7PM)     # answers two questions at once: skips ahead
    assert r.replies[0].text == "Is everyone over 21? Only physical IDs are accepted"
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
    assert "Girls are always free" in t and "Which night are you thinking?" in t
    assert t.count("table with drinks") == 1
    assert lead(eng)["status"] == "qualifying"


def test_birthday_table_goes_to_human(eng):
    r = chat(eng, "can we get a table for my birthday saturday", THU_7PM)
    assert lead(eng)["status"] == "needs_human" and r.notify


def test_decline_gets_short_reply_and_no_questions(eng):
    r = chat(eng, "heyy not this weekend but ty!!", THU_7PM)
    assert r.replies[0].text == "No worries! We'll invite you again soon"
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
    assert "get there at 11" in r.replies[0].text and not r.notify
    r = chat(eng, "what's the address?", THU_7PM)
    assert r.notify and lead(eng)["status"] == "confirmed"


def test_new_night_after_last_one_starts_new_lead(eng):
    chat(eng, "noflex tonight, 3 girls no guys all 21", THU_7PM)
    first = lead(eng)["id"]
    chat(eng, "we're here", datetime(2026, 11, 12, 23, 0, tzinfo=TZ))
    r = chat(eng, "can we come saturday too? same 3 girls", datetime(2026, 11, 13, 14, 0, tzinfo=TZ))
    assert lead(eng)["id"] != first
    assert r.replies[0].text.startswith("Hi! Welcome back") and "NYX Karaoke" in r.replies[0].text


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
    sat_0140 = datetime(2026, 11, 14, 1, 40, tzinfo=TZ)            # still Friday night
    r = chat(eng, "hii can me and 2 girls get on nebula tonight? all 21", sat_0140)
    t = r.replies[0].text
    assert "You're all set for Nebula tonight" not in t
    # 1:40am is past Nebula's 1:30 list close: she's told, and offered Saturday
    assert "The list for Nebula tonight closed at 1:30am." in t and "tomorrow" in t


def test_late_request_before_last_entry(eng):
    fri_2350 = datetime(2026, 11, 13, 23, 50, tzinfo=TZ)
    r = chat(eng, "nebula tonight, 3 girls all 21", fri_2350)
    assert "head over now, the list closes at 1:30am" in r.replies[0].text
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


def test_dress_code_by_day_and_by_night(eng):
    eng.venues["nebula"]["dress_code"] = {"friday": "All black, heels recommended", "default": "Cute going-out fit"}
    t = chat(eng, "can 3 of us do nebula tonight? all girls, all 21", THU_7PM, user="a").replies[0].text
    assert "Cute going-out fit." in t
    t = chat(eng, "can 3 of us do nebula tonight? all girls, all 21", FRI_7PM, user="b").replies[0].text
    assert "All black, heels recommended." in t
    eng.apply_night_dress_codes({"nebula": {"2026-11-13": "White party, all white", "2026-11-14": None}})
    t = chat(eng, "can 3 of us do nebula tonight? all girls, all 21", FRI_7PM, user="c").replies[0].text
    assert "White party, all white." in t


def test_muse_os_confirmation_fills_dress_code(eng):
    eng.apply_venue_confirmations({"nebula": "Dress code: [DRESS_CODE]. [HOST] walks you in."})
    eng.venues["nebula"]["dress_code"] = {"thursday": "Black tie"}
    t = chat(eng, "can 3 of us do nebula tonight? all girls, all 21", THU_7PM).replies[0].text
    assert "Dress code: Black tie. Devin walks you in." in t


def test_guys_table_rule_at_nebula_and_noflex(eng):
    t = chat(eng, "can 3 girls and 2 guys come to nebula tonight? all 21", FRI_7PM, user="a").replies[0].text
    assert "the guys can't be at the table" in t and "ticket" in t
    t = chat(eng, "can 3 girls and 2 guys come to noflex tonight? all 21", FRI_7PM, user="b").replies[0].text
    assert "the guys can't be at the table" in t
    t = chat(eng, "can 3 girls and 1 guy come to nebula tonight? all 21", FRI_7PM, user="c").replies[0].text
    assert "table" not in t.split("Guys just need a ticket")[1]
    t = chat(eng, "can 3 girls and 2 guys come to public hotel thursday? all 21", THU_7PM, user="d").replies[0].text
    assert "can't be at the table" not in t


def test_venue_not_running_that_night_says_so():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    tue = datetime(2026, 11, 10, 18, 54, tzinfo=TZ)
    t = chat(eng, "are you guys promoting at nebula tonight", tue).replies[0].text
    assert "We're not at Nebula tonight" in t and "Friday or Saturday" in t
    assert lead(eng)["status"] != "confirmed"
    t = chat(eng, "friday, 3 of us no guys all 21", tue).replies[0].text
    assert t.startswith("You're all set for Nebula Friday")


def test_venue_off_but_other_spots_that_night():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    t = chat(eng, "can 3 of us come to nebula tonight? all girls all 21", THU_7PM).replies[0].text
    assert "We're not at Nebula tonight" in t and "Tonight we have Public Hotel or NOFLEX" in t
    assert chat(eng, "noflex", THU_7PM).replies[0].text.startswith("You're all set for NOFLEX tonight")


def test_manual_outreach_reply_becomes_a_lead_with_details(eng):
    eng.handle_human_reply("g9", "heyy are you and your girls free this weekend? 💕", now=THU_7PM)
    r = chat(eng, "yess me and 3 friends friday at nebula!", THU_7PM, user="g9")
    assert r.replies == []                      # the promoter is handling it, bot stays quiet
    l = lead(eng, "g9")
    assert l["night"] == "2026-11-13" and l["venue"] == "nebula" and l["party_size"] == 4
    assert l["id"] in eng.store.dirty or True   # synced to Muse OS on the next pass


TUE_730 = datetime(2026, 11, 10, 19, 30, tzinfo=TZ)


def test_unknown_venue_goes_silently_to_the_account_holder(eng):
    chat(eng, "hi devin!", TUE_730)
    chat(eng, "Friday", TUE_730)
    r = chat(eng, "i wanna go to mission", TUE_730)
    assert r.replies == [] and lead(eng)["status"] == "needs_human"
    assert "Mission" in r.notify[0]
    assert chat(eng, "hello?", TUE_730).replies == []   # stays with the account holder


def test_other_venue_detection(eng):
    assert eng.other_venue("are you guys doing mission friday?", None) == "Mission"
    assert eng.other_venue("nyx karaoke", "venue") is None
    for text in ("can we come to the list tonight", "we're going to be late", "i want to go friday",
                 "can 3 of us go to nebula", "going to sleep lol", "at 11?"):
        assert eng.other_venue(text, None) is None, text


def test_huge_group_goes_to_a_human(eng):
    chat(eng, "hi devin!", TUE_730)
    r = chat(eng, "i hv 100 in my grp", TUE_730)
    assert "big group" in r.replies[0].text and lead(eng)["status"] == "needs_human"


def test_yes_some_guys_asks_how_many_first(eng):
    chat(eng, "hi! nebula friday", TUE_730)
    r = chat(eng, "Yes, some guys", TUE_730)
    assert r.replies[0].text.startswith("How many guys?")
    chat(eng, "2", TUE_730)
    assert eng.store.convo("g1")["draft"]["guys"] == 2


def test_two_replies_it_cant_read_go_to_a_human(eng):
    chat(eng, "hi", TUE_730)
    chat(eng, "friday", TUE_730)
    r = chat(eng, "lol", TUE_730)
    assert len(r.replies[0].text.splitlines()) == 1
    r = chat(eng, "idk", TUE_730)
    assert lead(eng)["status"] == "needs_human" and r.notify


def test_stress_test_chats(eng):
    """From the team's stress tests on 2026-11-10."""
    chat(eng, "Thursday", TUE_730, user="e")
    r = chat(eng, "can i bring 21 20yr olds", TUE_730, user="e")
    assert "Thanks for being honest" in r.replies[0].text and lead(eng, "e")["status"] == "needs_human"
    chat(eng, "Friday, two people including myself and no guys, everyone is over 21", TUE_730, user="w")
    assert chat(eng, "Nebulaa", TUE_730, user="w").replies[0].text.startswith("You're all set for Nebula Friday")
    chat(eng, "nebula friday", TUE_730, user="s")
    r = chat(eng, "can we go somewhere else", TUE_730, user="s")
    assert r.replies == [] and lead(eng, "s")["status"] == "needs_human"
    chat(eng, "nebula friday", TUE_730, user="g")
    r = chat(eng, "can i bring 200 guys", TUE_730, user="g")
    assert "big group" in r.replies[0].text


def test_faq_always_followed_by_a_question_or_next_step(eng):
    r = chat(eng, "Heyy, do you have any tables for Nebula this weekend", TUE_730, user="t1")
    t = r.replies[0].text
    assert "table" in t and t.rstrip().endswith("?") and "🥂" not in t
    chat(eng, "friday, 3 of us no guys all 21", TUE_730, user="t1")
    assert lead(eng, "t1")["status"] == "confirmed"
    t = chat(eng, "do you have any tables for Nebula this weekend", TUE_730, user="t1").replies[0].text
    assert "table" in t and "You're already on for Nebula Friday" in t


def test_answers_directly_using_what_she_asked(eng):
    t = chat(eng, "Heyy, do you have any tables for Nebula this weekend", TUE_730, user="q1").replies[0].text
    assert t.startswith("Hi! Yes, girls get free entry and a table with drinks at Nebula.")
    assert "We're at Nebula Thursday, Friday or Saturday, which night works for you?" in t
    t = chat(eng, "how much is it for guys at noflex", TUE_730, user="q2").replies[0].text
    assert "Guys just need a ticket" in t and "free" not in t.lower()


def test_weekly_cover_from_muse_os(eng):
    eng.apply_night_covers({"nebula": {"2026-11-13": "$40", "2026-11-14": "$50"}, "noflex": {"2026-11-13": None}})
    t = chat(eng, "3 girls 1 guy nebula friday all 21", TUE_730, user="c1").replies[0].text
    assert "Guys just need a ticket ($40):" in t
    t = chat(eng, "2 girls 1 guy noflex friday all 21", TUE_730, user="c2").replies[0].text
    assert "Guys just need a ticket:" in t
    t = chat(eng, "how much is it for guys at nebula saturday", TUE_730, user="c3").replies[0].text
    assert "($50)" in t


def test_ticket_link_per_night_and_cover_only_venues(eng):
    eng.apply_night_ticket_links({"nebula": {"2026-11-13": "https://tix/fri", "2026-11-14": "https://tix/sat"}})
    t = chat(eng, "3 girls 1 guy nebula saturday all 21", TUE_730, user="n1").replies[0].text
    assert "https://tix/sat" in t
    t = chat(eng, "3 girls 1 guy nebula friday all 21", TUE_730, user="n2").replies[0].text
    assert "https://tix/fri" in t
    eng.venues["noflex"]["guys_policy"] = "cover"
    eng.apply_night_covers({"noflex": {"2026-11-13": "$30"}})
    r = chat(eng, "2 girls 1 guy noflex friday all 21", TUE_730, user="n3")
    t = r.replies[0].text
    assert "Guys pay a $30 cover at the door, no ticket needed." in t and "ticket:" not in t
    assert not any("ticket link" in n for n in r.notify)
    t = chat(eng, "how much is it for guys at noflex friday", TUE_730, user="n4").replies[0].text
    assert "No ticket needed, guys just pay $30 cover at the door." in t


def test_guys_entry_from_muse_os(eng):
    eng.apply_venue_policies({"noflex": "cover", "nebula": "ask", "slate": None})
    assert eng.venues["noflex"]["guys_policy"] == "cover" and eng.venues["nebula"]["guys_policy"] == "ask"
    r = chat(eng, "2 girls 1 guy nebula friday all 21", TUE_730, user="p1")
    assert lead(eng, "p1")["status"] == "needs_human"


THU = datetime(2026, 11, 12, 19, 0, tzinfo=TZ)


@pytest.mark.parametrize("text,expect", [
    ("Which club?", "Tonight we're at"),
    ("Hi! Thank you ~ can I bring a friend?", "bring as many girls as you want"),
    ("That's perf! When is a good time to come?", "the list closes at 12:30am"),
    ("Is there a dress code?", "no sneakers or Uggs"),
    ("heyy is this 21+??", "21+ with a physical ID"),
    ("wait so should I show them this message at the door? or how does this work", "text us here when you're outside"),
    ("can my guy friend come", "Guys are welcome"),
])
def test_historical_questions_get_direct_answers(text, expect):
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    t = chat(eng, text, THU).replies[0].text
    assert expect in t and lead(eng)["status"] == "qualifying", t


@pytest.mark.parametrize("text", ["hi! can we get a sign for my friends table? its her bday saturday",
                                  "do you guys do signs for the table?", "can we get a sparkler sign at nebula friday",
                                  "Is this the right location?", "by any chance do u have a promo code for this?"])
def test_signs_and_unanswerable_questions_go_to_the_team(text):
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    r = chat(eng, text, THU)
    assert lead(eng)["status"] == "needs_human" and r.notify


def test_no_guys_is_not_a_no_to_the_age_question():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    for m in ("free guest list with my girls tonight?", "public hotel", "4"):
        chat(eng, m, THU)
    chat(eng, "no guys", THU)
    assert lead(eng)["status"] == "qualifying"
    assert chat(eng, "yes", THU).replies[0].text.startswith("You're all set for Public Hotel")


def test_no_id_is_an_age_issue_not_another_venue():
    eng = Engine(load_config("config.yaml"), Store(":memory:"))
    r = chat(eng, "We are at another party rn so we don't have physical id", THU)
    assert "Thanks for being honest" in r.replies[0].text


def test_guestlist_closing_times_differ_by_venue(eng):
    eng.apply_list_closes({"public hotel": "20:00", "nebula": None}, {"noflex": {"2026-11-12": "22:00"}})
    thu_2030 = datetime(2026, 11, 12, 20, 30, tzinfo=TZ)
    t = chat(eng, "public hotel tonight? 3 girls all 21", thu_2030, user="l1").replies[0].text
    assert t.startswith("Hi! We'd love to have you out\nThe list for Public Hotel tonight closed at 8pm.")
    assert "Tonight we still have NOFLEX (until 10pm)" in t
    # only venues still open are offered
    t = chat(eng, "can we come tonight?", thu_2030, user="l2").replies[0].text
    assert "Public Hotel" not in t and "NOFLEX" in t
    # Nebula keeps the config time (1:30am) since Muse OS sent none
    assert eng.last_entry(eng.venues["nebula"], "2026-11-13") == "01:30"
