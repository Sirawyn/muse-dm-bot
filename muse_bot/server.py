"""Web server that connects the engine to Instagram through Meta's official API.

META_LOGIN picks the Meta setup:
  unified   (default) "API setup with Unified login". IG_ACCESS_TOKEN = the token Meta generates for the Muse
            Instagram account, IG_ACCOUNT_ID = its Instagram account id. No Facebook Page needed. The bot tries
            graph.facebook.com then graph.instagram.com and keeps whichever accepts the token. A graph.instagram.com token is renewed weekly; a
            graph.facebook.com one is checked daily and the team is warned 10 days before it expires.
  facebook "API setup with Facebook login": the Muse Instagram is linked to a Facebook Page.
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
META_LOGIN = os.getenv("META_LOGIN", "unified").strip().lower()
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
_VER = os.getenv("GRAPH_VERSION", "v23.0")
_HOST = "graph.instagram.com" if META_LOGIN == "instagram" else "graph.facebook.com"
GRAPH = f"https://{_HOST}/{_VER}"
SEND_AS = IG_ACCOUNT_ID if META_LOGIN in ("instagram", "unified") else (FB_PAGE_ID or "me")   # who the DM is sent from


def graph_bases() -> list[str]:
    """Where to call Meta. Unified login is new and Meta's docs differ on the host, so try both and keep
    whichever answers (remembered in the settings table)."""
    if META_LOGIN != "unified":
        return [GRAPH]
    bases = [f"https://graph.facebook.com/{_VER}", f"https://graph.instagram.com/{_VER}"]
    good = engine.store.get_setting("graph_base")
    return [good] + [b for b in bases if b != good] if good in bases else bases

engine = Engine(load_config(os.getenv("MUSE_CONFIG", "config.yaml")), Store(os.getenv("MUSE_DB", "muse_leads.db")))


def token() -> str:
    """The Instagram token: the latest auto-renewed one, unless IG_ACCESS_TOKEN was changed since."""
    if engine.store.get_setting("ig_token_from_env") != ENV_TOKEN:   # someone pasted a new token: start from it
        engine.store.set_setting("ig_token_from_env", ENV_TOKEN)
        engine.store.set_setting("ig_token", ENV_TOKEN)
        engine.store.set_setting("ig_token_refreshed_at", datetime.now(timezone.utc).isoformat())
    return engine.store.get_setting("ig_token") or ENV_TOKEN


async def token_expiry() -> dict:
    """Ask Meta when the token expires (debug_token on graph.facebook.com). expires_at 0 = never."""
    r = await http.get(f"https://graph.facebook.com/{_VER}/debug_token",
                       params={"input_token": token(), "access_token": token()})
    if r.status_code >= 400:
        return {"error": r.status_code}
    d = r.json().get("data") or {}
    exp = int(d.get("expires_at") or 0)
    out = {"valid": d.get("is_valid"), "expires": "never" if not exp else
           datetime.fromtimestamp(exp, timezone.utc).date().isoformat()}
    if exp:
        out["days_left"] = (datetime.fromtimestamp(exp, timezone.utc) - datetime.now(timezone.utc)).days
    return out


def _uses_instagram_host() -> bool:
    return META_LOGIN == "instagram" or (META_LOGIN == "unified" and
                                         (engine.store.get_setting("graph_base") or "").startswith("https://graph.instagram.com"))


async def refresh_token_if_due() -> None:
    """Keep the token alive.
    Instagram-host tokens last 60 days: renew weekly so they never lapse (must be at least a day old).
    Facebook-host tokens (what Unified login gave us) can't be renewed by the bot, so check the expiry
    daily and warn the team 10 days ahead, then daily, so someone pastes a new IG_ACCESS_TOKEN in time."""
    if META_LOGIN == "facebook" or not token():
        return
    now = datetime.now(timezone.utc)
    if not _uses_instagram_host():
        if engine.store.get_setting("ig_token_checked_on") == now.date().isoformat():
            return
        engine.store.set_setting("ig_token_checked_on", now.date().isoformat())
        try:
            info = await token_expiry()
        except httpx.HTTPError as e:
            log.error("token expiry check failed: %s", e)
            return
        days = info.get("days_left")
        if info.get("valid") is False or (days is not None and days <= 10):
            when = "has expired" if info.get("valid") is False or days < 0 else f"expires in {days} days ({info['expires']})"
            await notify([f"⚠️ The Instagram token {when}. In the Meta app go to Instagram > API setup, generate a "
                          "new token for the Muse account and paste it into IG_ACCESS_TOKEN on Render, or the bot "
                          "will stop replying."])
        return
    last = engine.store.get_setting("ig_token_refreshed_at")
    if last and now - datetime.fromisoformat(last) < timedelta(days=7):
        return
    r = await http.get("https://graph.instagram.com/refresh_access_token",
                       params={"grant_type": "ig_refresh_token", "access_token": token()})
    if r.status_code >= 400:
        log.error("token refresh failed %s: %s", r.status_code, r.text[:200])
        await notify(["⚠️ The Instagram token couldn't be renewed. Generate a new one in the Meta app and update "
                      "IG_ACCESS_TOKEN, or the bot will stop replying."])
        return
    engine.store.set_setting("ig_token", r.json()["access_token"])
    engine.store.set_setting("ig_token_refreshed_at", now.isoformat())
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
    for base in graph_bases():
        try:
            r = await http.get(f"{base}/{ig_user_id}", params={"fields": "username,name"},
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
    r = None
    for base in graph_bases():
        r = await http.post(f"{base}/{SEND_AS}/messages",
                            headers={"Authorization": f"Bearer {token()}"},
                            json={"recipient": {"id": ig_user_id}, "message": msg})
        if r.status_code < 400:
            if META_LOGIN == "unified" and engine.store.get_setting("graph_base") != base:
                engine.store.set_setting("graph_base", base)
                log.info("Meta replies on %s, using it from now on", base)
            break
        log.error("send via %s failed %s: %s", base, r.status_code, r.text)
    if r is None or r.status_code >= 400:
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


_PRIVACY = os.path.join(os.path.dirname(__file__), "static", "privacy.html")


@app.get("/privacy")
@app.get("/data-deletion")
async def privacy():
    """Privacy policy and data deletion instructions, for the Meta app settings."""
    from fastapi.responses import FileResponse
    return FileResponse(_PRIVACY, media_type="text/html")


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
        events = list(entry.get("messaging", []))
        # some Instagram setups deliver DMs as "changes" with field "messages" instead of "messaging"
        events += [c.get("value") or {} for c in entry.get("changes", []) if c.get("field") == "messages"]
        for ev in events:
            msg = ev.get("message") or {}
            text = msg.get("text")
            if not text or msg.get("is_deleted"):
                continue  # stickers, reactions, story replies without text: leave for humans
            raw_ts = int(ev.get("timestamp") or 0)
            ts = datetime.fromtimestamp(raw_ts / 1000 if raw_ts > 10**11 else raw_ts, timezone.utc)
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


@app.get("/check-meta")
async def check_meta(token: str = ""):
    """Open /check-meta?token=<ADMIN_TOKEN> after setup: shows whether Meta accepts the token on each host."""
    check_admin(token)
    out = {"meta_login": META_LOGIN, "account_id_set": bool(SEND_AS and SEND_AS != "me"), "hosts": {}}
    for base in graph_bases():
        try:
            r = await http.get(f"{base}/{SEND_AS}", params={"fields": "id,username"},
                               headers={"Authorization": f"Bearer {globals()['token']()}"})
            out["hosts"][base] = r.json() if r.status_code < 400 else {"error": r.status_code, "detail": r.text[:300]}
        except httpx.HTTPError as e:
            out["hosts"][base] = {"error": str(e)}
    try:
        out["token_expiry"] = await token_expiry()
    except httpx.HTTPError as e:
        out["token_expiry"] = {"error": str(e)}
    return out


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
                        engine.apply_night_dress_codes(await asyncio.to_thread(museos.venue_dress_codes))
                        engine.apply_night_covers(await asyncio.to_thread(museos.venue_covers))
                        engine.apply_night_ticket_links(await asyncio.to_thread(museos.venue_night_links))
                        engine.apply_venue_policies(await asyncio.to_thread(museos.venue_policies))
                        engine.apply_list_closes(*(await asyncio.to_thread(museos.list_closes)))
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
