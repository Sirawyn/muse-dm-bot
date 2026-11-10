"""Copied from muse-os/integrations/museos.py (owned by the Muse OS app). Local changes: _girls_arrived uses the
host's exact count from a door check-in when the bot has one, and venue_hosts() reads the lineup's host per night.

Connects the DM bot to the Muse OS web app's API.

Bot setup (env): MUSE_OS_URL=https://os.example.com  MUSE_OS_API_KEY=muse_...  (Admin > DM bot API in Muse OS)

    from .museos import MuseOSSync
    museos = MuseOSSync.from_env(engine.tz) if os.getenv("MUSE_OS_URL") else None

New: checkins(since) returns the bookings hosts checked in / marked no-show in the app, so the bot can send the
right thank-you or "we missed you" (apply with engine.check_in / mark no-show; entries can repeat).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from urllib.parse import quote

log = logging.getLogger("muse.museos")

STATUS = {  # bot lead status -> Muse OS lead status
    "qualifying": "Qualifying", "needs_human": "Qualified", "confirmed": "Confirmed",
    "arrived": "Arrived", "no_show": "No-show", "dropped": "Lost", "cancelled": "Lost",
}
BOOKING_STATUS = {"confirmed": "Confirmed", "arrived": "Arrived", "no_show": "No-show", "cancelled": "Cancelled"}


def lead_key(lead_id: int) -> str:
    return f"DM-{lead_id:05d}"


def booking_key(lead_id: int) -> str:
    return f"BK-DM-{lead_id:05d}"


def _dt(v):
    return datetime.fromisoformat(v).strftime("%Y-%m-%d %H:%M") if v else None


def _d(v):
    return date.fromisoformat(v[:10]).isoformat() if v else None


class MuseOSSync:
    def __init__(self, base_url: str, api_key: str, tz=None, session=None):
        if session is None:
            import requests
            session = requests.Session()
        self.base = base_url.rstrip("/")
        self.s = session
        self.h = {"Authorization": f"Bearer {api_key}"}
        self.tz = tz
        self._checkin_cursor = ""

    @classmethod
    def from_env(cls, tz=None):
        return cls(os.environ["MUSE_OS_URL"], os.environ["MUSE_OS_API_KEY"], tz)

    def _put(self, path: str, body: dict) -> dict:
        r = self.s.put(self.base + path, json=body, headers=self.h, timeout=10)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = self.s.get(self.base + path, headers=self.h, timeout=10)
        r.raise_for_status()
        return r.json()

    # ---- leads, bookings, venues, scouts ----
    def sync_lead(self, lead: dict, convo: dict, venues: dict) -> None:
        v = venues.get(lead.get("venue") or "", {}) or {}
        party, guys = lead.get("party_size"), lead.get("guys")
        girls = party - guys if party is not None and guys is not None else None
        status = STATUS.get(lead["status"], "New")
        if status == "Qualifying" and not lead.get("first_reply_at"):
            status = "New"
        handle = (convo.get("username") or "").lower().lstrip("@") or None
        body = {
            "created_at": _dt(lead.get("first_msg_at")), "channel": "Instagram", "ig_handle": handle,
            "name": convo.get("name") or None, "night": _d(lead.get("night")), "venue": v.get("name"),
            "party_size": party, "girls": girls, "guys": guys, "status": status, "host": v.get("host"),
            "first_reply_at": _dt(lead.get("first_reply_at")), "last_message_at": _dt(convo.get("last_user_msg_at")),
            "notes": lead.get("notes") or None, "referred_by": lead.get("referred_by") or None,
        }
        self._put(f"/api/v1/leads/{lead_key(lead['id'])}", {k: x for k, x in body.items() if x is not None})
        if lead["status"] in BOOKING_STATUS and handle and lead.get("night") and v.get("name"):
            fu = lead.get("followup_sent")
            body = {"night": _d(lead["night"]), "venue": v["name"], "ig_handle": handle, "lead_id": lead_key(lead["id"]),
                    "host": v.get("host"), "stop": 1, "party_size": party, "girls": girls, "guys": guys,
                    "status": BOOKING_STATUS[lead["status"]], "confirmation_sent": True,
                    "thank_you_sent": fu == "thank_you"}
            arrived = self._girls_arrived(lead, girls)
            if arrived is not None:
                body["girls_arrived"] = arrived
            self._put(f"/api/v1/bookings/{booking_key(lead['id'])}", {k: x for k, x in body.items() if x is not None})

    @staticmethod
    def _girls_arrived(lead: dict, girls):
        if lead["status"] == "no_show":
            return 0
        if lead["status"] != "arrived":
            return None
        if lead.get("girls_arrived") is not None:   # exact count from a host check-in in Muse OS
            return lead["girls_arrived"]
        came = lead.get("arrived_count")
        if came is None:
            return girls
        return min(came, girls) if girls is not None else max(0, came - (lead.get("guys") or 0))

    def _venues(self) -> list[dict]:
        return self._get("/api/v1/venues")["venues"]

    def venue_nights(self) -> dict[str, list[str]]:
        return {v["name"].lower(): v["nights"] for v in self._venues()}

    def venue_confirmations(self) -> dict[str, str]:
        return {v["name"].lower(): v["confirmation_message"] for v in self._venues() if v["confirmation_message"]}

    def venue_ticket_links(self) -> dict[str, str]:
        return {v["name"].lower(): v["ticket_link"] for v in self._venues()}

    def venue_hosts(self) -> dict[str, dict[str, str | None]]:
        """Local addition: host on duty per venue and night, from venues[].upcoming (next 14 lineup nights)."""
        return {v["name"].lower(): {u["night"]: u.get("host") for u in v.get("upcoming") or []} for v in self._venues()}

    def scouts(self) -> list[dict]:
        return self._get("/api/v1/scouts")["scouts"]

    # ---- new ----
    def checkins(self) -> list[dict]:
        """Bookings settled at the door since the last call: [{lead_id: 'DM-00007', status: 'Arrived'|'No-show'|
        'Confirmed', girls_arrived}]. Only DM- leads (the bot's own) are returned."""
        r = self._get("/api/v1/checkins?since=" + quote(self._checkin_cursor))
        self._checkin_cursor = r["now"]
        return [b for b in r["bookings"] if (b.get("lead_id") or "").startswith("DM-")]
