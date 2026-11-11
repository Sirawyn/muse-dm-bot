"""End to end: DM bot engine + muse_bot.museos against the real Muse OS app.
Run from the muse-os folder: cd ../muse-os && python -m pytest -q ../muse-dm-bot/integration --rootdir=../muse-dm-bot/integration"""
import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "/mnt/project-files/muse-os")
sys.path.insert(0, "/mnt/project-files/muse-dm-bot")
os.environ.setdefault("MUSE_INSECURE_COOKIES", "1")
from tests.conftest import con, client, csrf, login  # noqa: F401  (muse-os fixtures)
from app import auth
from muse_bot.engine import Engine, load_config
from muse_bot.museos import MuseOSSync
from muse_bot.store import Store

TZ = ZoneInfo("America/New_York")
FRI = datetime(2026, 11, 13, 19, 0, tzinfo=TZ)


def sync_all(eng, s):
    for i in eng.store.pop_dirty():
        lead = eng.store.lead(i)
        s.sync_lead(lead, eng.store.convo(lead["ig_user_id"]), eng.venues)


def test_e2e(client, con):
    key = auth.create_api_key(con, "DM bot")
    con.execute("UPDATE venues SET confirmation_message='Heels please. [HOST] will walk you in.' WHERE name='Nebula'")
    con.commit()
    s = MuseOSSync("", key, session=client)
    eng = Engine(load_config("/mnt/project-files/muse-dm-bot/config.yaml"), Store(":memory:"))
    eng.apply_venue_schedule(s.venue_nights())
    eng.apply_venue_confirmations(s.venue_confirmations())
    eng.apply_venue_ticket_links(s.venue_ticket_links())
    eng.apply_scouts(s.scouts())
    eng.apply_night_hosts(s.venue_hosts())
    assert "nebula" in eng.venues_on(FRI.date())

    r = eng.handle_message("111", "jess sent me! nebula tonight, me and 3 girls, all 21+", now=FRI, username="Amy.B")
    assert "Devin will walk you in" in r.replies[0].text, r.replies
    r2 = eng.handle_message("222", "nebula tonight 2 girls all 21", now=FRI, username="zoe")
    sync_all(eng, s)
    row = con.execute("SELECT status, referred_by, venue, girls FROM leads WHERE id='DM-00001'").fetchone()
    assert tuple(row) == ("Confirmed", "Jess", "Nebula", 4)
    assert con.execute("SELECT status FROM bookings WHERE id='BK-DM-00002'").fetchone()[0] == "Confirmed"

    # hosts settle the door in the app: Amy's group 3 of 4 girls, Zoe no-show
    login(client, "boss")   # bookings are hosted by Devin here, so an admin settles the door
    client.post("/bookings/BK-DM-00001/checkin", data={"csrf": csrf(client), "action": "arrived", "count": "3"})
    client.post("/bookings/BK-DM-00002/checkin", data={"csrf": csrf(client), "action": "no_show"})
    entries = s.checkins()
    assert eng.apply_checkins(entries) == 2
    assert eng.apply_checkins(entries) == 0                       # idempotent
    a, z = eng.store.lead(1), eng.store.lead(2)
    assert (a["status"], a["girls_arrived"]) == ("arrived", 3) and z["status"] == "no_show"

    sent = dict(eng.run_jobs(now=datetime(2026, 11, 14, 13, 5, tzinfo=TZ)))
    assert sent["111"].text.startswith("Thank you for coming out") and "missed you" in sent["222"].text
    sync_all(eng, s)
    b = con.execute("SELECT status, girls_arrived, thank_you_sent FROM bookings WHERE id='BK-DM-00001'").fetchone()
    assert tuple(b) == ("Arrived", 3, 1)


def test_weekly_fields_reach_the_bot(client, con):
    """Per-night ticket link, cover, dress code and the venue's Guys' entry, set in Muse OS, show up in replies."""
    key = auth.create_api_key(con, "DM bot")
    con.execute("INSERT INTO nights(night, venue, host, dress_code, cover, ticket_link, updated_at) "
                "SELECT '2026-11-14', name, NULL, 'All black', '$50', 'https://tix.example/sat', '' FROM venues "
                "WHERE name='Nebula' ON CONFLICT(night, venue) DO UPDATE SET dress_code='All black', cover='$50', "
                "ticket_link='https://tix.example/sat'")
    con.commit()
    s = MuseOSSync("", key, session=client)
    eng = Engine(load_config("/mnt/project-files/muse-dm-bot/config.yaml"), Store(":memory:"))
    eng.apply_night_hosts(s.venue_hosts())
    eng.apply_night_dress_codes(s.venue_dress_codes())
    eng.apply_night_covers(s.venue_covers())
    eng.apply_night_ticket_links(s.venue_night_links())
    eng.apply_venue_policies(s.venue_policies())
    t = eng.handle_message("333", "nebula saturday, 3 girls 1 guy, all 21", now=FRI, username="kim").replies[0].text
    assert "All black" in t and "($50)" in t and "https://tix.example/sat" in t, t


def test_list_closes_reach_the_bot(client, con):
    """Guestlist closing time per venue and per night, set in Muse OS, is what the bot quotes."""
    key = auth.create_api_key(con, "DM bot")
    con.execute("UPDATE venues SET list_closes='01:00' WHERE name='Nebula'")
    con.execute("INSERT INTO nights(night, venue, host, list_closes, updated_at) "
                "SELECT '2026-11-14', name, NULL, '00:45', '' FROM venues WHERE name='Nebula' "
                "ON CONFLICT(night, venue) DO UPDATE SET list_closes='00:45'")
    con.commit()
    s = MuseOSSync("", key, session=client)
    by_venue, by_night = s.list_closes()
    assert by_venue["nebula"] == "01:00" and by_night["nebula"]["2026-11-14"] == "00:45"
    eng = Engine(load_config("/mnt/project-files/muse-dm-bot/config.yaml"), Store(":memory:"))
    eng.apply_list_closes(by_venue, by_night)
    t = " ".join(r.text for r in eng.handle_message("444", "what time does the list close for nebula saturday", now=FRI,
                                                    username="ana").replies)
    assert "12:45" in t, t


def test_guys_free_reaches_the_bot(client, con):
    """Guys' entry Free, set for one night in Muse OS, beats the venue's ticket setting."""
    key = auth.create_api_key(con, "DM bot")
    con.execute("INSERT INTO nights(night, venue, host, guys_policy, updated_at) "
                "SELECT '2026-11-14', name, NULL, 'Free', '' FROM venues WHERE name='Nebula' "
                "ON CONFLICT(night, venue) DO UPDATE SET guys_policy='Free'")
    con.commit()
    s = MuseOSSync("", key, session=client)
    assert s.night_policies()["nebula"]["2026-11-14"] == "free"
    eng = Engine(load_config("/mnt/project-files/muse-dm-bot/config.yaml"), Store(":memory:"))
    eng.apply_venue_policies(s.venue_policies())
    eng.apply_night_policies(s.night_policies())
    t = eng.handle_message("555", "nebula saturday, 3 girls 1 guy, all 21", now=FRI, username="zoe").replies[0].text
    assert "Guys get in free too" in t, t
