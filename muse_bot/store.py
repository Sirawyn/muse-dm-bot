"""SQLite storage: one row per conversation, one row per lead (a guest's request for one night),
and an event log of every message in and out."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    ig_user_id TEXT PRIMARY KEY,
    username TEXT,
    name TEXT,
    stage TEXT NOT NULL DEFAULT 'new',
    expecting TEXT,
    lead_id INTEGER,
    paused_until TEXT,
    last_user_msg_at TEXT,
    draft TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ig_user_id TEXT NOT NULL,
    username TEXT,
    night TEXT,
    venue TEXT,
    party_size INTEGER,
    guys INTEGER,
    status TEXT NOT NULL,          -- qualifying | needs_human | confirmed | arrived | no_show | cancelled
    first_msg_at TEXT,
    first_reply_at TEXT,
    response_seconds REAL,
    confirmed_at TEXT,
    arrived_at TEXT,
    arrived_count INTEGER,
    followup_sent TEXT,            -- thank_you | missed_you | skipped_outside_24h
    followup_at TEXT,
    notes TEXT,
    referred_by TEXT,              -- scout (Team name) who sent her
    girls_arrived INTEGER          -- exact count from a host's door check-in in Muse OS
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    ig_user_id TEXT,
    direction TEXT,                -- in | out | human | system
    text TEXT,
    mid TEXT
);
CREATE INDEX IF NOT EXISTS leads_night ON leads(night);
CREATE INDEX IF NOT EXISTS leads_user ON leads(ig_user_id);
"""


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = "muse_leads.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(leads)")}
        for col, typ in (("referred_by", "TEXT"), ("girls_arrived", "INTEGER")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE leads ADD COLUMN {col} {typ}")
        self.dirty: set[int] = set()   # lead ids changed since the last Muse OS sync

    def pop_dirty(self) -> list[int]:
        ids, self.dirty = sorted(self.dirty), set()
        return ids

    # conversations
    def convo(self, ig_user_id: str) -> dict:
        row = self.db.execute("SELECT * FROM conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO conversations (ig_user_id) VALUES (?)", (ig_user_id,))
            self.db.commit()
            return self.convo(ig_user_id)
        c = dict(row)
        c["draft"] = json.loads(c["draft"] or "{}")
        return c

    def save_convo(self, c: dict) -> None:
        self.db.execute(
            """UPDATE conversations SET username=?, name=?, stage=?, expecting=?, lead_id=?, paused_until=?,
               last_user_msg_at=?, draft=? WHERE ig_user_id=?""",
            (c.get("username"), c.get("name"), c["stage"], c.get("expecting"), c.get("lead_id"), c.get("paused_until"),
             c.get("last_user_msg_at"), json.dumps(c.get("draft") or {}), c["ig_user_id"]),
        )
        self.db.commit()

    # leads
    def new_lead(self, ig_user_id: str, username: str | None, first_msg_at: datetime) -> int:
        cur = self.db.execute(
            "INSERT INTO leads (ig_user_id, username, status, first_msg_at) VALUES (?,?,?,?)",
            (ig_user_id, username, "qualifying", iso(first_msg_at)),
        )
        self.db.commit()
        self.dirty.add(cur.lastrowid)
        return cur.lastrowid

    def lead(self, lead_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def update_lead(self, lead_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE leads SET {cols} WHERE id=?", (*fields.values(), lead_id))
        self.db.commit()
        self.dirty.add(lead_id)

    def leads(self, where: str = "1=1", params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(f"SELECT * FROM leads WHERE {where} ORDER BY id", params)]

    # events
    def log(self, at: datetime, ig_user_id: str | None, direction: str, text: str, mid: str | None = None):
        self.db.execute(
            "INSERT INTO events (at, ig_user_id, direction, text, mid) VALUES (?,?,?,?,?)",
            (iso(at), ig_user_id, direction, text, mid),
        )
        self.db.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    def sent_mid(self, mid: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM events WHERE mid=? AND direction='out'", (mid,)).fetchone() is not None
