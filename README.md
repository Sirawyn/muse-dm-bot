# Muse DM bot

Replies to Instagram DMs within seconds. It asks the qualifying questions, sends the venue confirmation and
the morning-after thank-you, and logs every guest so you can track response times and no-shows.

## What it does

```
Guest DMs  ──► bot replies in seconds, asking everything it still needs in ONE message
               (night, venue, party size, any guys?) with tap-to-answer buttons
           ──► guest answers ──► door-policy check for guys
                   ├─ OK ─────────► venue confirmation (dress code, ID, arrive by, host walks you in)
                   └─ not sure ───► "let me check with the door" + alert to the team
           ──► "we're here" ──► "Devin is on the way out" + alert to the host
           ──► next day 1pm ──► thank-you (showed up) or "we missed you" (no-show)
```

* **Trained on the real DM history (1,000 chats).** It reads how guests actually write ("me + 2 other girls",
  "myself n 3 girls", "we’re otw", "we're all 21"), answers the questions the team answers every night
  (girls are free, table and drinks included, guys buy a ticket, get there after 11 and before 12) instead of
  handing them off, asks "Is everyone over 21? Only physical IDs are accepted" before confirming, and answers
  "not this weekend" with "No worries! We'll invite you again soon 🫶" instead of the greeting.
  `tests/test_real_messages.py` runs the bot on the real guest phrasings.
* **Hands off to a human** for birthdays and bachelorettes, buying tables or bottles, discounts, complaints,
  job inquiries and anyone under 21 or without a physical ID. Also guys at strict
  doors and any question it can't answer after confirming. The team gets an alert in Slack or Discord.
* **Credits scouts.** If a DM mentions a scout's @handle or full name, or their first name next to words like
  "sent me", "from" or "told me", the lead is tagged with that scout ("Referred by" on the lead in Muse OS) so
  they get their $5 per girl who arrives. Scouts come from the Team page in Muse OS (role Scout).
* **Steps back when a promoter types by hand.** If anyone replies from the Instagram app, the bot goes quiet in
  that chat for 12 hours, so it never talks over your team.
* **Knows the weekly schedule.** It offers only the venues running that night. If only one venue is on, it
  skips the question. If nothing is on, it offers the nights that are.
* **Logs every lead**: first-message time, how fast it was answered, venue, party size, guys, confirmed,
  arrived, headcount, no-show, follow-up sent. Download it as a CSV from `/leads.csv`, or open `/report` for
  median response time, the funnel, no-show rate by venue, and guests with 2 or more no-shows.

* **Feeds Muse OS.** Every conversation becomes a lead in the Muse OS web app (`DM-00001` style ids, updated as
  the status moves New → Qualifying → Confirmed → Arrived / No-show / Lost). New guests are added (existing guests
  are left alone, so tags and notes stay), and each confirmed group gets a booking with the host on duty.
  Every 5 minutes the bot reads from Muse OS each venue's nights, confirmation message (`[HOST]` becomes that
  night's host), guys' ticket link and the active scouts. Guys' ticket links change weekly: supervisors send them
  by Monday morning and they go on the Venues page. At noon on Monday the team gets an alert for any venue running
  that week with no link. When hosts check a group in or mark a no-show at the door in Muse OS, the bot picks it
  up, so she gets the right next-day thank-you or "we missed you".

Everything the bot says is in **`config.yaml`**: venues, hosts, arrival times, dress codes, door policy for guys,
the weekly schedule and every message. Change that file to change the bot. Lines marked `TODO` are my guesses
for venues other than Nebula. Confirm them before going live.

## Instagram's rules (built in)

* **24-hour window.** A business can send automated messages only within 24 hours of the guest's last message.
  The bot sends follow-ups at 1pm the next day. A guest who texted "we're here" at 11pm is well inside the window.
  If the window has closed, it skips the message and logs `skipped_outside_24h`.
* **It only replies.** The bot never DMs anyone first. Cold outreach by bot gets accounts restricted.
* **A human is always reachable.** Anything off-script goes to a person.
* Uses Meta's official API only. No scraping, no unofficial auto-DM tools. Those are what get accounts banned.

## Recommended path

| | ManyChat | This bot (official Meta API) |
|---|---|---|
| Live in | an afternoon | 1–3 weeks (Meta app review) |
| Cost | monthly fee that rises with contact count | about $5–10/mo of hosting |
| Venue-specific logic, guys policy, schedule | partly, by hand per flow | yes, from one config file |
| Your own lead and no-show database | export only | built in, synced to Muse OS |
| Multiple promoters/hosts, routing later | limited | yours to extend |

**Do both:** set up ManyChat this week so you stop losing girls to faster promoters, using the exact messages in
`config.yaml`. At the same time, submit the Meta app for this bot. Switch over once it's approved. Only one of them
can be connected to the account at a time.

### ManyChat quick setup (this week)
1. Sign up at manychat.com, connect the Muse Instagram account (it must be a Professional account).
2. Automation → **Default Reply**: send the `greeting` + questions from `config.yaml`, use "User Input" blocks to
   save night / party size / guys into custom fields, then a Condition block per venue that sends that venue's
   `confirmation` text.
3. Add a **Keyword** automation for "here / outside / we're here" that sends `arrival_ack` and notifies the host.

### Going live with this bot
1. **Instagram**: switch @muse to a Professional (Business or Creator) account. In Settings → Messages, turn on
   *Allow access to messages* for connected tools.
2. **Meta app**: on developers.facebook.com create an app (Use case: *Manage messaging & content on Instagram*) →
   *API setup with Unified login*. Add the Muse Instagram account and generate its token (`IG_ACCESS_TOKEN`).
   Copy the Instagram account ID shown next to it (`IG_ACCOUNT_ID`, a long number starting 178…) and, under
   App settings > Basic, the app secret (`META_APP_SECRET`). No Facebook Page is needed.
3. **Host the bot.** Easiest: put this folder in a GitHub repo, then on render.com choose New > Blueprint and
   pick the repo. `render.yaml` sets up the server and a small disk for the guest database (Render's Starter
   plan with a disk is about $7–8/month). Any host that runs Docker or Python with HTTPS also works:
   ```
   pip install -r requirements.txt
   uvicorn muse_bot.server:app --host 0.0.0.0 --port 8000
   ```
   Set the environment variables: `IG_ACCESS_TOKEN`, `IG_ACCOUNT_ID`, `META_APP_SECRET`, `VERIFY_TOKEN` (any
   password you choose), `ADMIN_TOKEN` (another password, for the team links) and, optionally,
   `NOTIFY_WEBHOOK_URL` (a Slack or Discord channel webhook for team alerts).
   `META_LOGIN` is already set to `unified`. After it deploys, open `https://<your-host>/check-meta?token=<ADMIN_TOKEN>`:
   it shows whether Meta accepts the token. The bot tries graph.facebook.com and graph.instagram.com and keeps
   whichever works. (Older apps: `META_LOGIN=instagram` or `facebook` still work.)
4. **Muse OS**: in Muse OS go to Admin > DM bot API > Create key, then set `MUSE_OS_URL` (the app's address)
   and `MUSE_OS_API_KEY` on the bot. Without them the bot still replies, but leads stay only in its own database.
5. **Webhook**: in the Meta app, set the callback URL to `https://<your-host>/webhook` with your `VERIFY_TOKEN`
   and subscribe to the `messages` field.
6. **App review**: request Advanced Access for `instagram_business_basic` and
   `instagram_business_manage_messages`. Submit a screen recording of the simulator conversation below.
   Until it's approved, the bot only answers accounts that have a role on the app, which is fine for testing
   with your own team.

### Team links (need `ADMIN_TOKEN`)
* `GET /report?token=…`: response times, funnel, no-shows by venue and guest.
* `GET /leads.csv?token=…`: the full lead log for Sheets/Excel.
* `POST /leads/<id>/checkin?count=4`: host checks a group in (if they didn't text "here").
* `POST /leads/<id>/confirm`: after you clear a handed-off request (e.g. the door OK'd the guys), sends the confirmation.
* `POST /guests/<ig_user_id>/resume`: turn the bot back on in a chat you took over.

## Try it without Instagram

```
pip install -r requirements.txt
python simulate.py            # chat as a guest in your terminal
python -m pytest -q tests     # 125 automated checks, including real guest messages
```
In the simulator, `/time 2026-11-12 23:20` moves the clock, `/human hi` acts like a promoter typing,
`/checkin` checks the guest in, `/jobs` runs the next-day follow-ups and no-show sweep, and `/report` shows stats.

## Files
* `config.yaml`: venues, schedule, messages (the only file you need to edit)
* `muse_bot/engine.py`: conversation flow
* `muse_bot/parser.py`: reads "me and 3 friends, no guys, noflex tonight" style messages
* `muse_bot/store.py`: SQLite lead log (`muse_leads.db`)
* `muse_bot/server.py`: Instagram webhook, sending, scheduled follow-ups, team links
* `muse_bot/reports.py`: stats
* `muse_bot/museos.py`: Muse OS app sync and door check-ins (copied from muse-os/integrations)
* `integration/test_museos_e2e.py`: runs the bot against the real Muse OS app
