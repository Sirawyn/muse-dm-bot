"""The conversation brain. No network code lives here, so it can be tested and simulated offline.

Flow for each guest and night:
  new DM -> qualify (night, venue, party size, guys) -> confirmation
         -> "we're here" text (or host check-in) -> arrived
         -> next afternoon: thank-you (arrived) or missed-you (no-show), only inside Instagram's 24h window
Anything the bot shouldn't handle (tables, bottles, birthdays, pricing, guys at strict doors)
goes to a human, and the bot goes quiet in any chat where a promoter has typed by hand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from .parser import WEEKDAYS, find_scout, night_for, parse
from .store import Store, iso


class _Blank(dict):
    def __missing__(self, key):
        return ""


@dataclass
class Out:
    text: str
    quick_replies: list[str] = field(default_factory=list)


@dataclass
class Result:
    replies: list[Out] = field(default_factory=list)
    notify: list[str] = field(default_factory=list)   # alerts for the team (hosts / admin)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class Engine:
    def __init__(self, config: dict, store: Store):
        self.cfg = config
        self.store = store
        self.tz = ZoneInfo(config["brand"].get("timezone", "America/New_York"))
        self.venues = config["venues"]
        self.msgs = config["messages"]
        # Scouts earn per girl they refer: [{"name": "Mia Lee", "handle": "mia.lee"}]. Loaded from Muse OS
        # (Team, role Scout), or listed in config under scouts:.
        self.scouts: list[dict] = list(config.get("scouts") or [])
        self.night_hosts: dict[str, dict[str, str]] = {}   # venue key -> {night: host on duty}
        self.lineup: dict[str, set[str]] = {}               # venue key -> nights on the Muse OS lineup
        self.night_dress: dict[str, dict[str, str]] = {}    # venue key -> {night: dress code} from Muse OS
        self.night_cover: dict[str, dict[str, str]] = {}    # venue key -> {night: guys' cover} from Muse OS
        self.night_policy: dict[str, dict[str, str]] = {}   # venue key -> {night: guys' entry} from Muse OS
        self.night_links: dict[str, dict[str, str]] = {}    # venue key -> {night: guys' ticket link} from Muse OS
        self.night_closes: dict[str, dict[str, str]] = {}   # venue key -> {night: guestlist closing "HH:MM"}
        self.lineup_dates: set[str] = set()

    # helpers
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def _local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz) if dt.tzinfo else dt.replace(tzinfo=self.tz)

    def venues_on(self, night: date) -> list[str]:
        """The dated lineup from Muse OS wins for the nights it covers (the next 14); after that, or without
        Muse OS, the weekly pattern."""
        if self.lineup_dates and night.isoformat() <= max(self.lineup_dates):
            return [k for k, nights in self.lineup.items() if night.isoformat() in nights]
        return list(self.cfg.get("schedule", {}).get(WEEKDAYS[night.weekday()], []) or [])

    def _offer_ask(self, choices: list[str], nights: list[str]) -> str:
        """'Want to come Thursday?' when there's one thing to offer, 'one of those' only when there are several."""
        if len(choices) == 1:
            key = "venue_off_ask_night" if nights else "venue_off_ask_venue"
            return self.fmt(key, choice=nights[0] if nights else choices[0])
        return self.msgs["venue_off_ask"]

    def night_label(self, night: date, now: datetime) -> str:
        base = night_for(now)
        if night == base:
            return "tonight"
        if night == base + timedelta(days=1):
            return "tomorrow"
        return WEEKDAYS[night.weekday()].capitalize()

    def upcoming_nights(self, now: datetime) -> list[tuple[date, list[str]]]:
        base = night_for(now)
        out = []
        for i in range(7):
            d = base + timedelta(days=i)
            if self.venues_on(d):
                out.append((d, self.venues_on(d)))
        return out

    def fmt(self, key: str, **kw) -> str:
        return self.msgs[key].format_map(_Blank(kw)).strip()

    @staticmethod
    def join_names(names: list[str]) -> str:
        if len(names) <= 1:
            return "".join(names)
        return ", ".join(names[:-1]) + " or " + names[-1]

    def _week_options(self, now: datetime, limit: int = 4) -> str:
        parts = []
        for d, keys in self.upcoming_nights(now)[:limit]:
            parts.append(f"{self.night_label(d, now)} ({' & '.join(self.venues[k]['name'] for k in keys)})")
        return self.join_names(parts) if parts else "a few spots soon"

    def _lead_is_live(self, lead: dict | None, now: datetime) -> bool:
        if not lead or lead["status"] in ("no_show", "cancelled", "dropped"):
            return False
        if lead["night"] and date.fromisoformat(lead["night"]) < night_for(now):
            return False
        return True

    # inbound DM
    def handle_message(self, ig_user_id: str, text: str, msg_time: datetime | None = None,
                       username: str | None = None, now: datetime | None = None,
                       name: str | None = None) -> Result:
        now = self._local(now or self.now())
        msg_time = self._local(msg_time or now)
        res = Result()
        c = self.store.convo(ig_user_id)
        c["username"] = username or c.get("username")
        c["name"] = name or c.get("name")
        c["last_user_msg_at"] = iso(msg_time)
        self.store.log(msg_time, ig_user_id, "in", text)
        who = f"@{c['username']}" if c.get("username") else ig_user_id

        lead = self.store.lead(c["lead_id"]) if c.get("lead_id") else None
        p = parse(text, now, self.venues, c.get("expecting"))

        # A new night mentioned after a finished/confirmed one starts a fresh request.
        if lead and lead["status"] in ("confirmed", "arrived") and p.night \
                and lead["night"] and p.night.isoformat() != lead["night"]:
            lead = None
        if not self._lead_is_live(lead, now):
            if p.pleasantry:
                self.store.save_convo(c)
                return res   # "ty!!", "sounds good" after a night or a decline: nothing to answer
            lead = self.store.lead(self.store.new_lead(ig_user_id, c.get("username"), msg_time))
            c.update(stage="qualifying", expecting=None, lead_id=lead["id"], draft={})

        paused = c.get("paused_until") and datetime.fromisoformat(c["paused_until"]) > now
        if paused:
            # A double text (her 2nd message since the promoter's last one) ends the hold-off, but only when
            # the bot can actually answer it; otherwise it stays quiet and the promoter keeps the chat.
            key = f"texts_since_human:{ig_user_id}"
            n = int(self.store.get_setting(key) or 0) + 1
            self.store.set_setting(key, str(n))
            if n >= 2 and self._can_answer(p, c):
                c["paused_until"] = None
                paused = False
        if paused:   # a promoter is handling this chat by hand: stay quiet, but still note what she asked for
            d = c["draft"]
            for k, val in (("night", p.night and p.night.isoformat()), ("venue", p.venue),
                           ("party_size", p.party_size), ("guys", p.guys)):
                if val is not None:
                    d[k] = val
            fields = {k: d[k] for k in ("night", "venue", "party_size", "guys") if d.get(k) is not None
                      and lead.get(k) != d[k]}
            if fields:
                self.store.update_lead(lead["id"], **fields)
            self.store.dirty.add(lead["id"])
            self.store.save_convo(c)
            return res

        self.store.dirty.add(lead["id"])   # "last message at" changed
        scout_note = None
        if self.scouts and not lead.get("referred_by"):
            scout = find_scout(text, self.scouts)
            if scout:
                self.store.update_lead(lead["id"], referred_by=scout)
                lead["referred_by"] = scout
                scout_note = f"🤝 {who} was referred by scout {scout}"
        status = lead["status"]
        if status in ("confirmed", "arrived"):
            res = self._after_confirm(c, lead, p, text, now, who)
        elif status == "needs_human":
            res.notify.append(f"💬 {who} wrote again while waiting on the team: \"{text}\"")
        else:
            res = self._qualify(c, lead, p, now, who)

        if scout_note:
            res.notify.append(scout_note)
        if res.replies and not lead.get("first_reply_at"):
            self._mark_first_reply(lead, now)
        for r in res.replies:
            self.store.log(now, ig_user_id, "out", r.text)
        self.store.save_convo(c)
        return res

    def _mark_first_reply(self, lead: dict, now: datetime) -> None:
        first = datetime.fromisoformat(lead["first_msg_at"])
        self.store.update_lead(lead["id"], first_reply_at=iso(now),
                               response_seconds=max(0.0, (now - first).total_seconds()))

    def _qualify(self, c: dict, lead: dict, p, now: datetime, who: str) -> Result:
        res = Result()
        d = c["draft"]
        first_touch = not lead.get("first_reply_at")
        before = {k: val for k, val in d.items() if k != "stalls"}
        if p.night:
            d["night"] = p.night.isoformat()
        if p.venue:
            d["venue"] = p.venue
        if p.party_size:
            d["party_size"] = p.party_size
        if p.guys is not None:
            d["guys"] = p.guys
            d.pop("guys_unknown", None)
        elif p.guys_yes_no_count:
            d["guys_unknown"] = True
        over21 = p.over21
        if over21 is None and "over21" not in d and c.get("expecting") == "age" \
                and re.match(r"^\W*(yes+|yeah|yea|yep|yup|ya+)\b", p.text):
            over21 = True   # "yes" to the over-21 question (only that question: "yes 2 guys" answers the guys one)
        if over21 is not None:
            d["over21"] = over21

        if p.unsure_count or (c.get("expecting") in ("party_size", "guys_count") and not p.party_size and re.match(
                r"^\W*(i'?m |im )?(not sure|unsure|idk|i don'?t know|dont know|no idea|tbd|not certain)( yet)?( tbh| lol)?\W*$",
                p.text.strip().lower())):
            self._to_human(c, lead, d, "handoff_unsure_count", res, who, reason="not sure how many are coming yet")
            return res
        if p.wants_human:
            self._to_human(c, lead, d, "handoff", res, who, reason="birthday, signs, bottles, discount, complaint or job")
            return res
        if p.declined and not (p.party_size or p.venue):
            res.replies.append(Out(self.msgs["decline"]))
            self.store.update_lead(lead["id"], status="cancelled", notes="said not this time")
            c.update(stage="done", expecting=None)
            return res
        if d.get("over21") is False:
            self._to_human(c, lead, d, "age_check", res, who, reason="someone under 21 or without a physical ID")
            return res
        other = None if p.venue else self.other_venue(p.text, c.get("expecting"))
        if other:   # a club we don't work with: the account holder answers, the bot stays out of it
            self._to_human(c, lead, d, None, res, who, reason=f"asked about {other}, a venue we don't work with")
            return res
        big = int(self.cfg["brand"].get("big_group", 15))
        if d.get("over21") is False:
            self._to_human(c, lead, d, "age_check", res, who, reason="someone under 21 or without a physical ID")
            return res
        if max(d.get("party_size") or 0, d.get("guys") or 0) > big:
            self._to_human(c, lead, d, "big_group", res, who, reason=f"group of {max(d.get('party_size') or 0, d.get('guys') or 0)}, too big for the bot")
            return res
        faq_lines = self.faq_answers(p, d, now)
        if d.get("night") or d.get("venue"):   # the next question already names the options
            faq_lines = [f for f in faq_lines if not f.startswith(self.cfg.get("faq", {}).get("schedule", "~")[:12])]

        night = date.fromisoformat(d["night"]) if d.get("night") else None
        questions: list[str] = []
        quick: list[str] = []
        expecting = None

        if night and d.get("venue") and d["venue"] in self.venues_on(night) and not self.list_open(d["venue"], night, now):
            vname = self.venues[d["venue"]]["name"]
            label = self.night_label(night, now)
            still = [k for k in self.venues_on(night) if self.list_open(k, night, now)]
            nxt = [n for n, keys in self.upcoming_nights(now) if d["venue"] in keys and n != night][:2]
            parts = [self.fmt("list_closed", venue=vname, night=label,
                              closes=self._clock(self.last_entry(self.venues[d["venue"]], night.isoformat())))]
            if still:
                parts.append(self.fmt("list_closed_alt", night=label.capitalize(), options=self.join_names(
                    [f"{self.venues[k]['name']} (until {self._clock(self.last_entry(self.venues[k], night.isoformat()))})"
                     for k in still])))
            if nxt:
                parts.append(self.fmt("venue_off_next", venue=vname,
                                      venue_nights=self.join_names([self.night_label(n, now) for n in nxt])))
            quick = [self.venues[k]["name"] for k in still] + [self.night_label(n, now).capitalize() for n in nxt]
            parts.append(self._offer_ask(quick, nights=[self.night_label(n, now) for n in nxt]))
            questions.append(" ".join(parts))
            if still:
                d.pop("venue"); expecting = "venue"
            else:
                d.pop("night"); expecting = "night"
        elif night and d.get("venue") and d["venue"] not in self.venues_on(night):
            # she named a venue we're not promoting that night: say so, offer its nights and that night's spots
            vname = self.venues[d["venue"]]["name"]
            label = self.night_label(night, now)
            its = [n for n, keys in self.upcoming_nights(now) if d["venue"] in keys and n != night][:3]
            others = self.venues_on(night)
            parts = [self.fmt("venue_off_night", venue=vname, night=label)]
            if its:
                parts.append(self.fmt("venue_off_next", venue=vname,
                                      venue_nights=self.join_names([self.night_label(n, now) for n in its])))
            if others:
                parts.append(self.fmt("venue_off_alt", night=label.capitalize() if label != "tonight" else "Tonight",
                                      options=self.join_names([self.venues[k]["name"] for k in others])))
            if not its and not others:
                parts.append(self.fmt("venue_off_week", options=self._week_options(now)))
            quick = [self.night_label(n, now).capitalize() for n in its] + [self.venues[k]["name"] for k in others]
            parts.append(self._offer_ask(quick, nights=[self.night_label(n, now) for n in its]))
            questions.append(" ".join(p for p in parts if p))
            if others:
                d.pop("venue")          # "friday" keeps asking venue for Friday; "noflex" keeps tonight
                expecting = "venue"
            else:
                d.pop("night")          # "friday" means this venue on Friday
                expecting = "night"
        elif night and not d.get("venue"):
            options = [k for k in self.venues_on(night) if self.list_open(k, night, now)]
            if not options:
                questions.append(self.fmt("no_venue_that_night", night=self.night_label(night, now),
                                          options=self._week_options(now)))
                quick = [self.night_label(n, now).capitalize() for n, _ in self.upcoming_nights(now)][:3]
                d.pop("night")
                expecting = "night"
            elif len(options) == 1:
                d["venue"] = options[0]   # only one spot that night, no need to ask
            else:
                names = [self.venues[k]["name"] for k in options]
                said_something = p.over21 is not None or p.party_size or p.guys is not None or p.night
                if c.get("expecting") == "venue" and not said_something:
                    questions.append(self.fmt("venue_reask", night=self.night_label(night, now),
                                              options=self.join_names(names)))
                else:
                    questions.append(self.fmt("ask_venue", night=self.night_label(night, now),
                                              options=self.join_names(names)))
                quick = names
                expecting = "venue"
        if not d.get("night") and not questions:
            its = [n for n, keys in self.upcoming_nights(now) if d.get("venue") in keys][:3]
            if its:   # "tables at nebula this weekend?" -> "We're at Nebula Friday or Saturday, which night?"
                questions.append(self.fmt("ask_night_for_venue", venue=self.venues[d["venue"]]["name"],
                                          venue_nights=self.join_names([self.night_label(n, now) for n in its])))
                quick = [self.night_label(n, now).capitalize() for n in its]
            else:
                questions.append(self.msgs["ask_night"])
                quick = [self.night_label(n, now).capitalize() for n, _ in self.upcoming_nights(now)][:3]
            expecting = "night"
        if not d.get("party_size"):
            questions.append(self.msgs["ask_party_size"])
            expecting = expecting or "party_size"
        if d.get("guys_unknown"):
            questions.append(self.msgs["ask_guys_count"])
            expecting = expecting or "guys_count"
        elif d.get("guys") is None:
            questions.append(self.msgs["ask_guys"])
            if not quick:
                quick = ["No guys, just girls", "Yes, some guys"]
            expecting = expecting or "guys"
        if "over21" not in d:
            questions.append(self.msgs["ask_age"])
            expecting = expecting or "age"

        self.store.update_lead(lead["id"], night=d.get("night"), venue=d.get("venue"),
                               party_size=d.get("party_size"), guys=d.get("guys"))
        if p.guys_yes_no_count and d.get("guys_unknown") and self.msgs["ask_guys_count"] in questions:
            # she just said "yes, some guys": a bare number next is the guys count, so ask that first
            questions.remove(self.msgs["ask_guys_count"])
            questions.insert(0, self.msgs["ask_guys_count"])
            expecting = "guys_count"
        # One question at a time, like a person texting. She can still answer several at once: anything she
        # volunteers is kept and the bot skips straight to whatever is still missing.
        questions = questions[:1]
        if expecting not in ("night", "venue"):
            quick = ["No guys, just girls", "Yes, some guys"] if expecting == "guys" else []
        progressed = ({k: val for k, val in d.items() if k != "stalls"} != before
                      or bool(p.night or p.venue or p.party_size or p.guys is not None or p.over21 is not None))
        asks_for_spot = re.search(r"\b(list|guest|come|join|go out|going out|tonight|tn|tmr|tomorrow|this week|weekend|"
                                  r"spot|room|space|free|table|interested|down)\b", p.text.lower())
        if questions and not progressed and not faq_lines and p.is_question and not asks_for_spot:
            # a question the bot can't answer: don't dodge it with "which night?", get a person
            self._to_human(c, lead, d, "handoff", res, who, reason=f"question the bot can't answer: \"{p.text}\"")
            return res
        if questions and not first_touch and not progressed and not faq_lines:
            # her reply didn't answer anything: never paste the same block again
            d["stalls"] = d.get("stalls", 0) + 1
            if d["stalls"] >= 2:
                self._to_human(c, lead, d, "handoff", res, who, reason=f"bot didn't understand: \"{p.text}\"")
                return res
            questions = questions[:1]
        elif progressed:
            d.pop("stalls", None)
        if questions:
            text = " ".join(q[:1].upper() + q[1:] for q in faq_lines + questions)   # one paragraph, reads like a person
            if first_touch:
                returning = len(self.store.leads("ig_user_id=?", (lead["ig_user_id"],))) > 1
                hello = self.msgs.get("greeting_returning") if returning else None
                if faq_lines and not hello:   # she asked something: answer it right away, no long hello
                    text = self.msgs.get("greeting_short", "Hi!").strip() + " " + text
                else:
                    text = (hello or self.msgs["greeting"]).strip() + " " + text
            res.replies.append(Out(text, quick if len(quick) <= 13 else []))
            c["expecting"] = expecting
            return res

        # Everything collected: check the door policy for guys.
        v = self.venues[d["venue"]]
        guys, party = d["guys"], d["party_size"]
        if guys >= party:
            self._to_human(c, lead, d, "handoff", res, who,
                           reason=f"group of {party} with {guys} guys, numbers need a human look")
            return res
        girls = party - guys
        scheduled = self.venues_on(night)
        if scheduled and d["venue"] not in scheduled:
            self._to_human(c, lead, d, "handoff", res, who,
                           reason=f"asked for {v['name']} on a night it's not on the schedule")
            return res
        policy = self.policy_for(d["venue"], d.get("night"), "ok")
        if guys and (policy in ("ask", "no") or
                     (policy == "ratio" and girls < guys * v.get("min_girls_per_guy", 2))):
            self._to_human(c, lead, d, "guys_check", res, who,
                           reason=f"{guys} guy(s) in a group of {party} for {v['name']}")
            return res
        if now >= self.night_time(night, self.last_entry(v, night.isoformat())):
            self._to_human(c, lead, d, "late_check", res, who,
                           reason=f"asked after the list closed ({self.last_entry(v, night.isoformat())}) for {v['name']}")
            return res
        self._confirm(c, lead, d, now, res, who, faq_lines)
        return res

    def night_time(self, night: date, hhmm: str) -> datetime:
        """A clock time on a night out: anything before noon belongs to the early hours after it."""
        h, m = (int(x) for x in hhmm.split(":"))
        day = night + timedelta(days=1) if h < 12 else night
        return datetime.combine(day, datetime.min.time(), self.tz) + timedelta(hours=h, minutes=m)

    def last_entry(self, v: dict, night: str | None = None) -> str:
        """When that venue's guestlist closes ("20:00", "01:30"): the night's time from Muse OS, then the venue's
        time from Muse OS, then config.yaml, then the brand default."""
        key = next((k for k, x in self.venues.items() if x is v or x.get("name") == v.get("name")), None)
        own = self.night_closes.get(key or "", {}).get(night or "")
        return str(own or v.get("list_closes_app") or v.get("list_closes") or v.get("last_entry")
                   or self.cfg["brand"].get("last_entry", "00:30"))

    def list_open(self, key: str, night: date, now: datetime) -> bool:
        return now < self.night_time(night, self.last_entry(self.venues[key], night.isoformat()))

    def apply_list_closes(self, by_name: dict[str, str | None], nights: dict[str, dict[str, str | None]]) -> None:
        """Guestlist closing times from Muse OS: per venue ("20:00") and per night overrides."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        for name, k in keys.items():
            t = by_name.get(name)
            self.venues[k]["list_closes_app"] = t if t and re.fullmatch(r"\d{1,2}:\d{2}", t) else None
        self.night_closes = {keys[n]: {d: t for d, t in ns.items() if t and re.fullmatch(r"\d{1,2}:\d{2}", t)}
                             for n, ns in nights.items() if n in keys}

    def faq_answers(self, p, d: dict | None = None, now: datetime | None = None) -> list[str]:
        faq = self.cfg.get("faq", {})
        v = self.venues.get((d or {}).get("venue") or "", {})
        if v and (d or {}).get("night"):
            dress = self.dress_code_for(d["venue"], date.fromisoformat(d["night"]))
        else:
            dress = str(v.get("dress_code") if v and not isinstance(v.get("dress_code"), dict) else
                        "Heels and dresses are recommended, no sneakers or Uggs").rstrip(".")
        now_ = now or self.now()
        parts = [(self.night_label(n, now_), " and ".join(self.venues[k]["name"] for k in keys))
                 for n, keys in self.upcoming_nights(now_)[:4]]
        week = ", ".join([f"{parts[0][0].capitalize()} we're at {parts[0][1]}"] +
                         [f"{'and ' if i == len(parts) - 1 else ''}{lbl} {names}" for i, (lbl, names) in
                          enumerate(parts) if i]) if parts else "We'll have new nights up soon"
        last = self._clock(self.last_entry(v, (d or {}).get("night"))) if v else self._clock(str(self.cfg["brand"].get("last_entry", "00:30")))
        link = self.ticket_link(v, (d or {}).get("night")) if v else self.ticket_link({})
        out = []
        keys = [k for k in p.faq if not (k == "table" and "free" in p.faq)]   # "free" already covers the table
        if self.guys_free((d or {}).get("venue"), (d or {}).get("night")) and "guys_cost_free" in faq:
            keys = ["guys_cost_free" if k == "guys_cost" else k for k in keys]
        elif self.policy_for((d or {}).get("venue"), (d or {}).get("night")) == "cover" and "guys_cost_cover" in faq:
            keys = ["guys_cost_cover" if k == "guys_cost" else k for k in keys]
        if not link and "guys_cost_nolink" in faq:
            keys = ["guys_cost_nolink" if k == "guys_cost" else k for k in keys]
        for key in keys:
            if key in faq:
                out.append(faq[key].format(ticket_link=link or "we'll send you the link",
                                           arrive_by=v.get("arrive_by", "after 11 and before 12"),
                                           at_venue=f" at {v['name']}" if v else "", dress_code=dress, week=week,
                                           last_entry=last,
                                           cover=self.cover_note((d or {}).get("venue"), (d or {}).get("night")),
                                           cover_amount=(c + " ") if (c := self.cover_for((d or {}).get("venue"),
                                                                    (d or {}).get("night"))) else "").strip())
        return out

    def _confirm(self, c, lead, d, now, res: Result, who: str, faq_lines: list[str] | None = None) -> None:
        night = date.fromisoformat(d["night"])
        v = {**self.venues[d["venue"]], "host": self.host_for(d["venue"], night)}
        kw = dict(venue=v["name"], night=self.night_label(night, now), party_size=d["party_size"],
                  dress_code=self.dress_code_for(d["venue"], night), arrive_by=v["arrive_by"], host=v["host"])
        late = now >= self.night_time(night, str(self.cfg["brand"].get("late_after", "23:30")))
        if late:   # it's already going: tell them to head over now, not "get there after 11"
            text = self.fmt("confirmation_late", last_entry=self._clock(self.last_entry(v, d["night"])), **kw)
        elif v.get("confirmation_text"):   # edited on the Venues page in Muse OS
            text = self.fmt("confirmation_intro", **kw) + "\n\n" + v["confirmation_text"].replace("[HOST]", v["host"]).replace("[DRESS_CODE]", kw["dress_code"])
        else:
            text = self.fmt("confirmation", **kw)
        if d.get("guys") and self.guys_free(d["venue"], d["night"]):
            text += "\n" + self.msgs["guys_free"]
        elif d.get("guys") and self.policy_for(d["venue"], d["night"]) == "cover":   # pay at the door, no ticket
            cover = self.cover_for(d["venue"], d["night"])
            text += "\n" + (self.fmt("guys_cover", cover=cover) if cover else self.msgs["guys_cover_unknown"])
        elif d.get("guys") and self.policy_for(d["venue"], d["night"], "ticket") == "ticket":
            link = self.ticket_link(self.venues[d["venue"]], d["night"])
            text += "\n" + self.fmt("guys_ticket" if link else "guys_ticket_nolink", ticket_link=link or "",
                                     cover=self.cover_note(d["venue"], d["night"]))
            if not link:
                res.notify.append(f"🎟️ Send {who} the guys' ticket link for {v['name']}")
        if (d.get("guys") or 0) >= 2 and str(v.get("guys_at_table", "")).lower() == "one":
            text += "\n" + self.fmt("guys_no_table")
        faq_lines = [f for f in (faq_lines or []) if not d.get("guys") or not re.search(r"ticket|cover", f.lower())]
        if faq_lines:
            text = "\n".join(faq_lines) + "\n\n" + text
        res.replies.append(Out(text))
        self.store.update_lead(lead["id"], status="confirmed", confirmed_at=iso(now),
                               party_size=d["party_size"], guys=d.get("guys"))
        c.update(stage="confirmed", expecting=None)
        res.notify.append(f"{'⏰ Late add, on the way now' if late else '✅ Confirmed'} {who}: "
                          f"{d['party_size']} ({d.get('guys') or 0} guys) for "
                          f"{v['name']} {WEEKDAYS[night.weekday()].capitalize()} {night}. Host: {v['host']}")

    @staticmethod
    def _clock(hhmm: str) -> str:
        h, m = (int(x) for x in hhmm.split(":"))
        return f"{(h % 12) or 12}{':%02d' % m if m else ''}{'am' if h < 12 else 'pm'}"

    def nightly_digest(self, now: datetime | None = None) -> list[str]:
        """Once a night at digest_time: each venue's list for tonight, under that night's host."""
        now = self._local(now or self.now())
        night = night_for(now)
        if now < self.night_time(night, str(self.cfg["brand"].get("digest_time", "21:00"))) or now.hour < 12:
            return []
        marker = f"digest {night.isoformat()}"
        if self.store.db.execute("SELECT 1 FROM events WHERE direction='system' AND text=?", (marker,)).fetchone():
            return []
        self.store.log(now, None, "system", marker)
        out = []
        leads = self.store.leads("night=? AND status IN ('confirmed','arrived')", (night.isoformat(),))
        for key in sorted({l["venue"] for l in leads if l["venue"]}):
            group = [l for l in leads if l["venue"] == key]
            guys = sum(l["guys"] or 0 for l in group)
            people = sum(l["party_size"] or 0 for l in group)
            names = ", ".join(f"@{l['username'] or l['ig_user_id']} ({l['party_size']})" for l in group)
            out.append(f"📋 Tonight at {self.venues[key]['name']} (host {self.host_for(key, night)}): "
                       f"{len(group)} groups, {people - guys} girls, {guys} guys. {names}")
        return out

    NOT_VENUES = {"the list", "list", "guestlist", "guest list", "the club", "club", "it", "you", "your", "sleep",
                  "bed", "work", "school", "the door", "door", "the table", "dinner", "the party", "party", "that",
                  "this", "there", "here", "one", "both", "either", "whichever", "any", "anything", "a club"}

    def other_venue(self, text: str, expecting: str | None) -> str | None:
        """A venue name we don't work with: "i wanna go to mission", "are you doing mission friday".
        Only when she names a place after go/going/come/doing, or answers the venue question with one."""
        t = re.sub(r"[^\w' ]", " ", text.lower())
        t = re.sub(r"\b(lol|lmao|haha+|pls|please|plz|rn|now|too|again|ok|okay)\b", " ", t)
        t = re.sub(r"\b(tonight|tomorrow|this|next|on)?\s*(mon|tues|wednes|thurs|fri|satur|sun)day\b|\btonight\b|\btmrw\b|\btomorrow\b|\b(this|next|the)?\s*weekend\b|\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", " ", t)
        if re.search(r"\bsomewhere else\b|\b(another|other|different) (club|venue|spot|place)\b", t):
            return "somewhere else"
        m = re.search(r"\b(?:go(?:ing)? to|come to|coming to|doing|at|promoting|promote)\s+((?:the\s+)?[a-z][a-z']*(?:\s+[a-z][a-z']*){0,2})\s*$", t.strip())
        name = m.group(1).strip() if m else None
        if not name and expecting == "venue":
            words = t.split()
            if 1 <= len(words) <= 3 and not re.search(r"\d|\b(yes|yeah|no|idk|lol|ok|okay|hm+|what|which|either|both|hi|hey)\b", t):
                name = " ".join(words)
        if not name or name in self.NOT_VENUES or name.removeprefix("the ") in self.NOT_VENUES:
            return None
        ours = {a.lower() for v in self.venues.values() for a in [v["name"], *v.get("aliases", [])]}
        if any(re.search(rf"\b{re.escape(a)}\b", name) for a in ours):
            return None
        if re.search(r"\b(guys|girls|people|friends|us|me|we|later|late|early|night|table|tables|list|guestlist|"
                     r"drinks?|bottles?|section|booth|party|event|thing|anything|something|promo|promos)\b", name):
            return None
        return name.title()

    def _to_human(self, c, lead, d, msg_key, res: Result, who: str, reason: str) -> None:
        v = self.venues.get(d.get("venue") or "", {})
        if msg_key:   # None = hand over without the bot saying anything
            res.replies.append(Out(self.fmt(msg_key, venue=v.get("name", "the venue"))))
        self.store.update_lead(lead["id"], status="needs_human", notes=reason, night=d.get("night"),
                               venue=d.get("venue"), party_size=d.get("party_size"), guys=d.get("guys"))
        c.update(stage="needs_human", expecting=None)
        res.notify.append(f"🙋 Needs a human: {who} ({reason}). Reply to them in the Instagram app.")

    def _after_confirm(self, c, lead, p, text, now, who) -> Result:
        res = Result()
        night = date.fromisoformat(lead["night"])
        v = {**self.venues[lead["venue"]], "host": self.host_for(lead["venue"], night)}
        if p.arrived and not p.is_question and (lead["status"] == "confirmed" or not p.on_the_way):
            if lead["status"] == "confirmed":
                self.store.update_lead(lead["id"], status="arrived", arrived_at=iso(now),
                                       arrived_count=lead["party_size"])
            key = "otw_ack" if p.on_the_way else "arrival_ack"
            res.replies.append(Out(self.fmt(key, host=v["host"])))
            verb = "is on the way to" if p.on_the_way else "is at"
            res.notify.append(f"🚪 {who} {verb} {v['name']} ({lead['party_size']} people). "
                              f"{v['host']}, get ready to walk them in!")
        elif p.over21 is False:
            self.store.update_lead(lead["id"], notes="age/ID issue after confirming")
            res.replies.append(Out(self.fmt("age_check")))
            res.notify.append(f"🪪 {who} (confirmed for {v['name']}) has an age/ID issue: \"{text}\"")
        elif p.declined:
            self.store.update_lead(lead["id"], status="cancelled", notes="cancelled by guest")
            res.replies.append(Out(self.msgs["decline"]))
            res.notify.append(f"❌ {who} cancelled for {v['name']} {night}")
        elif p.faq and not p.wants_human:
            # answer, then keep the conversation going: remind her she's already on the list
            follow = self.fmt("already_confirmed", venue=v["name"], night=self.night_label(night, now), host=v["host"])
            res.replies.append(Out("\n".join(self.faq_answers(p, {"venue": lead["venue"], "night": lead["night"]}) + [follow])))
        elif p.wants_human or p.is_question:
            res.replies.append(Out(self.fmt("handoff")))
            res.notify.append(f"❓ {who} (confirmed for {v['name']}) asked: \"{text}\"")
        elif lead["status"] == "confirmed" and (p.venue or p.party_size or p.night):
            res.replies.append(Out(self.fmt("already_confirmed", venue=v["name"],
                                            night=self.night_label(night, now), host=v["host"])))
        # Anything else ("thank you!!", emojis) gets no auto-reply so the bot doesn't over-talk.
        return res

    def _can_answer(self, p, c: dict) -> bool:
        """Would the bot have a real answer (not a handoff) for this message?"""
        if p.wants_human or p.declined or p.pleasantry or p.over21 is False:
            return False
        if not (p.night or p.venue or p.party_size or p.guys is not None or p.guys_yes_no_count or p.faq
                or p.over21 or p.arrived):
            return False   # nothing the bot understood: leave it to the promoter
        return not (not p.venue and self.other_venue(p.text, c.get("expecting")))

    # human promoter typed in the Instagram app
    def handle_human_reply(self, ig_user_id: str, text: str, now: datetime | None = None) -> None:
        """Hold the bot off this chat for human_takeover_minutes, restarting on every message the promoter sends."""
        now = self._local(now or self.now())
        c = self.store.convo(ig_user_id)
        mins = self.cfg["brand"].get("human_takeover_minutes")
        if mins is None:
            mins = 60 * self.cfg["brand"].get("human_takeover_hours", 12)
        c["paused_until"] = iso(now + timedelta(minutes=mins))
        self.store.set_setting(f"texts_since_human:{ig_user_id}", "0")
        self.store.log(now, ig_user_id, "human", text)
        lead = self.store.lead(c["lead_id"]) if c.get("lead_id") else None
        if lead and not lead.get("first_reply_at"):
            self._mark_first_reply(lead, now)
        self.store.save_convo(c)

    def apply_venue_confirmations(self, by_name: dict[str, str]) -> None:
        for v in self.venues.values():
            text = by_name.get(v["name"].lower())
            if text:
                v["confirmation_text"] = text

    def host_for(self, venue_key: str, night: date) -> str:
        """Host on duty from the Muse OS lineup (Nights tab), else the venue's config host."""
        name = self.night_hosts.get(venue_key, {}).get(night.isoformat()) or self.venues[venue_key]["host"]
        return (str(name).split() or [""])[0]   # first name only: never give guests an employee's full name

    def dress_code_for(self, venue_key: str, night: date) -> str:
        """That night's dress code: set for the night in Muse OS, else config by weekday, else the venue's default.
        In config.yaml dress_code is either one line, or a list by day: {friday: ..., saturday: ..., default: ...}."""
        own = self.night_dress.get(venue_key, {}).get(night.isoformat())
        dc = self.venues[venue_key].get("dress_code") or ""
        if not own and isinstance(dc, dict):
            own = dc.get(WEEKDAYS[night.weekday()]) or dc.get("default") or next(iter(dc.values()), "")
        return str(own or dc).strip().rstrip(".")

    def apply_night_covers(self, by_name: dict[str, dict[str, str | None]]) -> None:
        """{venue name lowercase: {"2026-11-13": "$40" or None}} from Muse OS venues[].upcoming[].cover."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        self.night_cover = {keys[n]: {d: str(c).strip() for d, c in nights.items() if c and str(c).strip()}
                            for n, nights in by_name.items() if n in keys}

    def cover_for(self, venue_key: str | None, night: str | None) -> str | None:
        """That night's guys' cover from Muse OS ("$40"), or None."""
        cover = self.night_cover.get(venue_key or "", {}).get(night or "")
        if not cover and venue_key and not night:   # no night yet: one cover for all its upcoming nights
            covers = set(self.night_cover.get(venue_key, {}).values())
            cover = covers.pop() if len(covers) == 1 else None
        return cover or None

    def policy_for(self, venue_key: str | None, night: str | None, default: str | None = None) -> str | None:
        """Guys' entry for that night: the night's own setting from Muse OS wins, else the venue's."""
        if not venue_key or venue_key not in self.venues:
            return default
        return self.night_policy.get(venue_key, {}).get(night or "") or self.venues[venue_key].get("guys_policy", default)

    def apply_night_policies(self, by_name: dict[str, dict[str, str | None]]) -> None:
        """{venue name lowercase: {"2026-11-13": "free"|"ticket"|"cover"|"ask"|None}} from venues[].upcoming[].guys_policy."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        self.night_policy = {keys[n]: {d: str(x).lower() for d, x in nights.items()
                                       if str(x or "").lower() in ("ticket", "cover", "ask", "free")}
                             for n, nights in by_name.items() if n in keys}

    def guys_free(self, venue_key: str | None, night: str | None) -> bool:
        """Guys get in free: Guys' entry set to free in Muse OS, or that night's cover entered as free/$0."""
        if not venue_key or venue_key not in self.venues:
            return False
        if self.policy_for(venue_key, night) in ("free", "ok"):
            return True
        cover = (self.cover_for(venue_key, night) or "").strip().lower().replace(" ", "")
        return cover in ("free", "$0", "0", "none", "nocover")

    def cover_note(self, venue_key: str | None, night: str | None) -> str:
        """' ($40)' when that night's cover is set in Muse OS, else ''."""
        cover = self.cover_for(venue_key, night)
        return f" ({cover})" if cover else ""

    def apply_night_dress_codes(self, by_name: dict[str, dict[str, str | None]]) -> None:
        """{venue name lowercase: {"2026-11-13": "All black" or None}} from Muse OS venues[].upcoming[].dress_code."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        self.night_dress = {keys[n]: {d: c for d, c in nights.items() if c}
                            for n, nights in by_name.items() if n in keys}

    def apply_night_hosts(self, by_name: dict[str, dict[str, str | None]]) -> None:
        """{venue name lowercase: {"2026-11-13": "Mia" or None}} from GET /api/v1/venues upcoming[]."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        self.night_hosts = {keys[n]: {d: h for d, h in nights.items() if h} for n, nights in by_name.items() if n in keys}
        # the dated lineup itself: which venue runs which night, host or not
        self.lineup = {keys[n]: set(nights) for n, nights in by_name.items() if n in keys}
        self.lineup_dates = {d for nights in self.lineup.values() for d in nights}

    def apply_scouts(self, scouts: list[dict]) -> None:
        if scouts:
            self.scouts = scouts

    def ticket_link(self, v: dict, night: str | None = None) -> str:
        """That night's link from Muse OS (each night can have its own, e.g. Nebula Friday vs Saturday), then the
        venue's weekly link from Muse OS, then the venue's config link, then the brand-wide one."""
        key = next((k for k, x in self.venues.items() if x is v or x.get("name") == v.get("name")), None)
        per_night = self.night_links.get(key or "", {}).get(night or "")
        return (per_night or v.get("ticket_link_app") or v.get("ticket_link")
                or self.cfg["brand"].get("ticket_link") or "")

    def apply_venue_policies(self, by_name: dict[str, str]) -> None:
        """Guys' entry chosen on the Venues page in Muse OS (ticket | cover | free | ask) overrides config.yaml."""
        for v in self.venues.values():
            pol = (by_name.get(v["name"].lower()) or "").lower()
            if pol in ("ticket", "cover", "ask", "free"):
                v["guys_policy"] = pol

    def apply_night_ticket_links(self, by_name: dict[str, dict[str, str | None]]) -> None:
        """{venue name lowercase: {"2026-11-13": "https://..." or None}} from Muse OS venues[].upcoming[].ticket_link."""
        keys = {v["name"].lower(): k for k, v in self.venues.items()}
        self.night_links = {keys[n]: {d: u.strip() for d, u in nights.items() if u and u.strip()}
                            for n, nights in by_name.items() if n in keys}

    def apply_venue_ticket_links(self, by_name: dict[str, str]) -> None:
        for v in self.venues.values():
            if v["name"].lower() in by_name:
                v["ticket_link_app"] = by_name[v["name"].lower()]

    def weekly_ticket_check(self, now: datetime | None = None) -> list[str]:
        """Once a week (Monday after `ticket_check_hour`), alert the team about venues running this week
        that still have no guys' ticket link."""
        now = self._local(now or self.now())
        hour = self.cfg["brand"].get("ticket_check_hour", 12)
        if now.weekday() != 0 or now.hour < hour:
            return []
        marker = f"ticket_check {now.date().isoformat()}"
        if self.store.db.execute("SELECT 1 FROM events WHERE direction='system' AND text=?", (marker,)).fetchone():
            return []
        self.store.log(now, None, "system", marker)
        missing = []
        for n, keys in self.upcoming_nights(now):
            for k in keys:
                if self.policy_for(k, n.isoformat(), "ticket") != "ticket" or self.guys_free(k, n.isoformat()):
                    continue   # cover at the door or no guys: no ticket link needed
                label = f"{self.venues[k]['name']} {WEEKDAYS[n.weekday()].capitalize()}"
                if not self.ticket_link(self.venues[k], n.isoformat()) and label not in missing:
                    missing.append(label)
        if not missing:
            return []
        return [f"🎟️ No guys' ticket link yet this week for {', '.join(missing)}. "
                f"Add them on the Weekly setup form in Muse OS."]

    def apply_venue_schedule(self, nights_by_name: dict[str, list[str]]) -> None:
        """Muse OS venue nights are the source of truth once any venue has nights filled in."""
        if not any(nights_by_name.values()):
            return
        by_name = {v["name"].lower(): k for k, v in self.venues.items()}
        sched = {d: [] for d in WEEKDAYS}
        for name, nights in nights_by_name.items():
            key = by_name.get(name)
            for n in nights:
                if key and n in sched:
                    sched[n].append(key)
        self.cfg["schedule"] = sched

    def resume_bot(self, ig_user_id: str) -> None:
        c = self.store.convo(ig_user_id)
        c["paused_until"] = None
        self.store.save_convo(c)

    # host tools
    def check_in(self, lead_id: int, count: int | None = None, now: datetime | None = None) -> None:
        now = self._local(now or self.now())
        lead = self.store.lead(lead_id)
        self.store.update_lead(lead_id, status="arrived", arrived_at=lead.get("arrived_at") or iso(now),
                               arrived_count=count if count is not None else lead["party_size"])

    def apply_checkins(self, entries: list[dict], now: datetime | None = None) -> int:
        """Door check-ins from the Muse OS app: [{lead_id: "DM-00007", status: "Arrived"|"No-show"|"Confirmed",
        girls_arrived}]. Safe to apply the same entry twice. Returns how many leads changed."""
        now = self._local(now or self.now())
        changed = 0
        for e in entries:
            m = re.fullmatch(r"DM-(\d+)", str(e.get("lead_id") or ""))
            lead = self.store.lead(int(m.group(1))) if m else None
            if not lead or lead["status"] in ("cancelled", "dropped"):
                continue
            st, girls = e.get("status"), e.get("girls_arrived")
            if st == "Arrived":
                want = dict(status="arrived", girls_arrived=girls,
                            arrived_count=(girls + (lead.get("guys") or 0)) if girls is not None else lead["party_size"])
                if not lead.get("arrived_at"):
                    want["arrived_at"] = iso(now)
            elif st == "No-show":
                want = dict(status="no_show", girls_arrived=0, arrived_count=0, arrived_at=None)
            elif st == "Confirmed":   # host undid a check-in
                want = dict(status="confirmed", girls_arrived=None, arrived_count=None, arrived_at=None)
            else:
                continue
            if any(lead.get(k) != v for k, v in want.items() if k != "arrived_at"):
                self.store.update_lead(lead["id"], **want)
                changed += 1
        return changed

    def confirm_manually(self, lead_id: int, now: datetime | None = None) -> Result:
        """After a human OKs a handed-off request (e.g. guys cleared with the door), send the confirmation."""
        now = self._local(now or self.now())
        lead = self.store.lead(lead_id)
        c = self.store.convo(lead["ig_user_id"])
        d = {"night": lead["night"], "venue": lead["venue"], "party_size": lead["party_size"], "guys": lead["guys"]}
        res = Result()
        who = f"@{lead['username']}" if lead.get("username") else lead["ig_user_id"]
        self._confirm(c, lead, d, now, res, who)
        c["paused_until"] = None
        self.store.save_convo(c)
        return res

    # scheduled jobs (run every few minutes)
    def run_jobs(self, now: datetime | None = None) -> list[tuple[str, Out]]:
        now = self._local(now or self.now())
        b = self.cfg["brand"]
        to_send: list[tuple[str, Out]] = []
        for lead in self.store.leads("status IN ('confirmed','qualifying','needs_human') AND night IS NOT NULL"):
            night = date.fromisoformat(lead["night"])
            cutoff = datetime.combine(night + timedelta(days=1), datetime.min.time(), self.tz) \
                + timedelta(hours=b.get("night_cutoff_hour", 5))
            if now >= cutoff:
                new = "no_show" if lead["status"] == "confirmed" else "dropped"
                self.store.update_lead(lead["id"], status=new)
        for lead in self.store.leads("status IN ('arrived','no_show') AND followup_sent IS NULL"):
            night = date.fromisoformat(lead["night"])
            due = datetime.combine(night + timedelta(days=1), datetime.min.time(), self.tz) \
                + timedelta(hours=b.get("followup_hour", 13))
            if now < due:
                continue
            c = self.store.convo(lead["ig_user_id"])
            last = datetime.fromisoformat(c["last_user_msg_at"]) if c.get("last_user_msg_at") else None
            # Instagram only allows automated messages within 24h of the guest's last message.
            if not last or now - last > timedelta(hours=23, minutes=30):
                self.store.update_lead(lead["id"], followup_sent="skipped_outside_24h", followup_at=iso(now))
                continue
            key = "thank_you" if lead["status"] == "arrived" else "missed_you"
            out = Out(self.fmt(key, next_options=self._week_options(now, limit=3)))
            to_send.append((lead["ig_user_id"], out))
            self.store.update_lead(lead["id"], followup_sent=key, followup_at=iso(now))
            self.store.log(now, lead["ig_user_id"], "out", out.text)
        return to_send
