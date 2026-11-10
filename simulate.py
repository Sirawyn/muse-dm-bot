"""Chat with the bot in your terminal, no Instagram needed.

  python simulate.py                 # chat as a guest
  Commands:  /human <text>   promoter types by hand     /checkin   host checks the guest in
             /time <YYYY-MM-DD HH:MM>   jump the clock  /jobs      run follow-ups + no-show sweep
             /report  stats   /quit
"""
import sys
from datetime import datetime

from muse_bot.engine import Engine, load_config
from muse_bot.reports import report
from muse_bot.store import Store

eng = Engine(load_config("config.yaml"), Store(":memory:"))
now = eng.now()
guest = "guest_1"


def show(res):
    for r in res.replies:
        print(f"\nMUSE: {r.text}")
        if r.quick_replies:
            print("      [" + "] [".join(r.quick_replies) + "]")
    for n in res.notify:
        print(f"   (team alert) {n}")


print(__doc__)
for line in sys.stdin if not sys.stdin.isatty() else iter(lambda: input("\nYOU: "), None):
    line = line.strip()
    if not sys.stdin.isatty():
        print(f"\nYOU: {line}")
    if not line:
        continue
    if line == "/quit":
        break
    if line.startswith("/time "):
        now = datetime.fromisoformat(line[6:]).replace(tzinfo=eng.tz)
        print(f"   (clock is now {now:%a %Y-%m-%d %H:%M})")
    elif line.startswith("/human "):
        eng.handle_human_reply(guest, line[7:], now=now)
        print("   (promoter replied by hand, bot paused for this chat)")
    elif line == "/checkin":
        c = eng.store.convo(guest)
        eng.check_in(c["lead_id"], now=now)
        print("   (host checked the guest in)")
    elif line == "/jobs":
        sent = eng.run_jobs(now=now)
        for _, out in sent:
            print(f"\nMUSE: {out.text}")
        if not sent:
            print("   (no follow-ups due)")
    elif line == "/report":
        print(report(eng.store))
    else:
        show(eng.handle_message(guest, line, now=now, username="sim_guest"))
