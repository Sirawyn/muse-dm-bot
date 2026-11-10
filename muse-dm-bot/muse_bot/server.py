"""Web server that connects the engine to Instagram through Meta's official API.

META_LOGIN picks the Meta setup:
  facebook  (default) "API setup with Facebook login": the Muse Instagram is linked to a Facebook Page.
            IG_ACCESS_TOKEN = the Page access token, FB_PAGE_ID = the Page's id. Calls go to graph.facebook.com.
  instagram the older "API setup with Instagram login" (only on apps made before late 2026).
            IG_ACCESS_TOKEN = Instagram user token, IG_ACCOUNT_ID = Instagram account id. Calls go to
            graph.instagram.com and the token is renewed weekly.

Run:  uvicorn muse_bot.server:app --host 0.0.0.0 --port 8000
Env:  IG_ACCESS_TOKEN, IG_ACCOUNT_ID, META_APP_SECRET, VERIFY_TOKEN,
      ADMIN_TOKEN (for the team endpoints), NOTIFY_WEBHOOK_URL (optional Slack/Discord webhook),
      MUSE_CONFIG (default config.yaml), MUSE_DB (default muse_leads.db), GRAPH_VERSION (default v23.0),
      MUSE_OS_URL + MUSE_OS_API_KEY (the Muse OS web app: leads, bookings, venues, scouts, door check-ins)
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from .engine import Engine, Out, load_config
from .reports import report
from .museos import MuseOSSync
from .store import Store

log = logging.getLogger("muse")
logging.basicConfig(level=logging.INFO)

ENV_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "")
APP_SECRET = os.getenv("META_APP_SECRET", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
NOTIFY_URL = os.getenv("NOTIFY_WEBHOOK_URL", "")
META_LOGIN = os.getenv("META_LOGIN", "facebook").strip().lower()
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
_HOST = "graph.instagram.com" if META_LOGIN == "instagram" else "graph.facebook.com"
GRAPH = f"https://{_HOST}/{os.getenv('GRAPH_VERSION', 'v23.0')}"
SEND_AS = IG_ACCOUNT_ID if META_LOGIN == "instagram" else (FB_PAGE_ID or "me")   # who the DM is sent from

engine = Engine(load_config(os.getenv("MUSE_CONFIG", "config.yaml")), Store(os.getenv("MUSE_DB", "muse_leads.db")))


def token() -> str:
    """The Instagram token: the latest auto-renewed one, unless IG_ACCESS_TOKEN was changed since."""
    if engine.store.get_setting("ig_token_from_env") != ENV_TOKEN:   # someone pasted a new token: start from it
        engine.store.set_setting("ig_token_from_env", ENV_TOKEN)
        engine.store.set_setting("ig_token", ENV_TOKEN)
        engine.store.set_setting("ig_token_refreshed_at", datetime.now(timezone.utc).isoformat())
    return engine.store.get_setting("ig_token") or ENV_TOKEN


async def refresh_token_if_due() -> None:
    """Instagram tokens last 60 days. Renew weekly so it never lapses (the token must be at least a day old)."""
    if META_LOGIN != "instagram":
        return   # a Page access token from a long-lived login doesn't expire
    last = engine.store.get_setting("ig_token_refreshed_at")
    if not token() or (last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=7)):
        return
    r = await http.get("https://graph.instagram.com/refresh_access_token",
                       params={"grant_type": "ig_refresh_token", "access_token": token()})
    if r.status_code >= 400:
        log.error("token refresh failed %s: %s", r.status_code, r.text)
        await notify(["⚠️ The Instagram token couldn't be renewed. Generate a new one in the Meta app and update "
                      "IG_ACCESS_TOKEN, or the bot will stop replying."])
        return
    engine.store.set_setting("ig_token", r.json()["access_token"])
    engine.store.set_setting("ig_token_refreshed_at", datetime.now(timezone.utc).isoformat())
    log.info("Instagram token renewed")
app = FastAPI(title="Muse DM bot")
http = httpx.AsyncClient(timeout=10)
# Muse OS web app is the system of record. Without MUSE_OS_URL the bot still replies; leads stay in its local db.
museos = MuseOSSync.from_env(engine.tz) if os.getenv("MUSE_OS_URL", "").startswith("http") else None


def _sync_now() -> None:
    for lead_id in engine.store.pop_dirty():
        lead = engine.store.lead(lead_id)
        try:
            museos.sync_lead(lead, engine.store.convo(lead["ig_user_id"]), engine.venues)
        except Exception:
            log.exception("Muse OS sync failed for lead %s, will retry", lead_id)
            engine.store.dirty.add(lead_id)


async def sync_app() -> None:
    """Push changed leads to Muse OS, off the request path so replies stay instant."""
    if museos:
        await asyncio.to_thread(_sync_now)


async def fetch_profile(ig_user_id: str) -> tuple[str | None, str | None]:
    """Instagram only sends a numeric id in webhooks, so look up the handle and name once per guest."""
    c = engine.store.convo(ig_user_id)
    if c.get("username"):
        return c["username"], c.get("name")
    try:
        r = await http.get(f"{GRAPH}/{ig_user_id}", params={"fields": "username,name"},
                           headers={"Authorization": f"Bearer {token()}"})
        if r.status_code < 400:
            j = r.json()
            return j.get("username"), j.get("name")
    except httpx.HTTPError as e:
        log.error("profile lookup failed: %s", e)
    return None, None


async def send(ig_user_id: str, out: Out) -> None:
    msg: dict = {"text": out.text}
    if out.quick_replies:
        msg["quick_replies"] = [{"content_type": "text", "title": q[:20], "payload": q[:20]}
                                for q in out.quick_replies[:13]]
    r = await http.post(f"{GRAPH}/{SEND_AS}/messages",
                        headers={"Authorization": f"Bearer {token()}"},
                        json={"recipient": {"id": ig_user_id}, "message": msg})
    if r.status_code >= 400:
        log.error("send failed %s: %s", r.status_code, r.text)
        return
    mid = r.json().get("message_id")
    if mid:
        engine.store.log(datetime.now(timezone.utc), ig_user_id, "out_mid", out.text, mid)


async def notify(lines: list[str]) -> None:
    for line in lines:
        log.info("TEAM: %s", line)
        if NOTIFY_URL:
            try:  # works with Slack ("text") and Discord ("content") incoming webhooks
                await http.post(NOTIFY_URL, json={"text": line, "content": line})
            except httpx.HTTPError as e:
                log.error("notify failed: %s", e)


def is_bot_echo(ig_user_id: str, text: str, mid: str | None) -> bool:
    db = engine.store.db
    if mid and db.execute("SELECT 1 FROM events WHERE mid=?", (mid,)).fetchone():
        return True
    since = (datetime.now(engine.tz) - timedelta(minutes=3)).isoformat(timespec="seconds")
    return db.execute("SELECT 1 FROM events WHERE ig_user_id=? AND direction='out' AND text=? AND at>=?",
                      (ig_user_id, text, since)).fetchone() is not None


def check_admin(token: str | None) -> None:
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(401, "bad admin token")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/webhook")
async def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403)


@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str = Header(default="")):
    body = await request.body()
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not APP_SECRET or not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(403, "bad signature")
    data = await request.json()
    for entry in data.get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message") or {}
            text = msg.get("text")
            if not text or msg.get("is_deleted"):
                continue  # stickers, reactions, story replies without text: leave for humans
            ts = datetime.fromtimestamp(ev.get("timestamp", 0) / 1000, timezone.utc)
            if msg.get("is_echo"):
                guest = ev["recipient"]["id"]
                if not is_bot_echo(guest, text, msg.get("mid")):
                    engine.handle_human_reply(guest, text)   # promoter typed by hand: bot steps back
                continue
            guest = ev["sender"]["id"]
            username, name = await fetch_profile(guest)
            res = engine.handle_message(guest, text, msg_time=ts, username=username, name=name)
            for out in res.replies:
                await send(guest, out)
            await notify(res.notify)
    asyncio.create_task(sync_app())
    return Response(status_code=200)


# team endpoints (send header X-Admin-Token)
@app.post("/leads/{lead_id}/checkin")
async def checkin(lead_id: int, count: int | None = None, x_admin_token: str = Header(default="")):
    check_admin(x_admin_token)
    engine.check_in(lead_id, count)
    await sync_app()
    return {"ok": True}


@app.post("/leads/{lead_id}/confirm")
async def confirm(lead_id: int, x_admin_token: str = Header(default="")):
    check_admin(x_admin_token)
    lead = engine.store.lead(lead_id)
    res = engine.confirm_manually(lead_id)
    for out in res.replies:
        await send(lead["ig_user_id"], out)
    await notify(res.notify)
    await sync_app()
    return {"ok": True}


@app.post("/guests/{ig_user_id}/resume")
async def resume(ig_user_id: str, x_admin_token: str = Header(default="")):
    check_admin(x_admin_token)
    engine.resume_bot(ig_user_id)
    return {"ok": True}


@app.get("/leads.csv")
async def leads_csv(token: str = ""):
    check_admin(token)
    rows = engine.store.leads()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@app.get("/report")
async def report_view(token: str = ""):
    check_admin(token)
    return PlainTextResponse(report(engine.store))


@app.on_event("startup")
async def start_jobs():
    async def loop():
        while True:
            try:
                if museos:
                    try:   # Muse OS being down must never stop follow-ups
                        engine.apply_venue_confirmations(await asyncio.to_thread(museos.venue_confirmations))
                        engine.apply_venue_schedule(await asyncio.to_thread(museos.venue_nights))
                        engine.apply_venue_ticket_links(await asyncio.to_thread(museos.venue_ticket_links))
                        engine.apply_scouts(await asyncio.to_thread(museos.scouts))
                        engine.apply_night_hosts(await asyncio.to_thread(museos.venue_hosts))   # who's on the door
                        engine.apply_checkins(await asyncio.to_thread(museos.checkins))         # door check-ins
                    except Exception:
                        log.exception("Muse OS read failed, using last known venues")
                await refresh_token_if_due()
                await notify(engine.weekly_ticket_check())
                await notify(engine.nightly_digest())
                for guest, out in engine.run_jobs():
                    await send(guest, out)
                await sync_app()
            except Exception:  # keep the loop alive
                log.exception("job loop error")
            await asyncio.sleep(300)
    asyncio.create_task(loop())
