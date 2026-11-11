"""Unified login: the bot falls back between Meta hosts and reads both webhook shapes."""
import hashlib, hmac, json, os, tempfile

import httpx
import pytest

os.environ.update(META_LOGIN="unified", IG_ACCESS_TOKEN="tok", IG_ACCOUNT_ID="17841400000000000",
                  META_APP_SECRET="sec", ADMIN_TOKEN="adm", MUSE_OS_URL="",
                  MUSE_DB=os.path.join(tempfile.mkdtemp(), "t.db"))
from fastapi.testclient import TestClient  # noqa: E402
from muse_bot import server  # noqa: E402

calls = []


def meta(req: httpx.Request) -> httpx.Response:
    calls.append((req.method, req.url.host, req.url.path))
    if req.url.host == "graph.facebook.com":
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token"}})
    if req.method == "POST":
        return httpx.Response(200, json={"recipient_id": "1", "message_id": f"m{len(calls)}"})
    return httpx.Response(200, json={"id": "1", "username": "jess"})


@pytest.fixture(autouse=True)
def fake_meta():
    server.http = httpx.AsyncClient(transport=httpx.MockTransport(meta))
    calls.clear()


def post(client, body):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(b"sec", raw, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig,
                                                         "Content-Type": "application/json"})


def test_unified_falls_back_and_remembers_host():
    with TestClient(server.app) as c:
        r = post(c, {"object": "instagram", "entry": [{"id": "17841400000000000", "messaging": [
            {"sender": {"id": "555"}, "recipient": {"id": "17841400000000000"}, "timestamp": 1762833600000,
             "message": {"mid": "x1", "text": "hi can i get on guestlist friday"}}]}]})
        assert r.status_code == 200
        sends = [h for m, h, p in calls if m == "POST" and p.endswith("/messages")]
        assert sends[:2] == ["graph.facebook.com", "graph.instagram.com"]
        assert server.engine.store.get_setting("graph_base").startswith("https://graph.instagram.com")
        calls.clear()
        post(c, {"object": "instagram", "entry": [{"id": "1", "messaging": [
            {"sender": {"id": "555"}, "recipient": {"id": "1"}, "timestamp": 1762833660000,
             "message": {"mid": "x2", "text": "3 girls no guys"}}]}]})
        assert [h for m, h, p in calls if m == "POST"][0] == "graph.instagram.com"


def test_changes_shaped_webhook_is_read():
    with TestClient(server.app) as c:
        post(c, {"object": "instagram", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "sender": {"id": "777"}, "recipient": {"id": "1"}, "timestamp": "1762833600",
            "message": {"mid": "y1", "text": "hey are you doing list tonight"}}}]}]})
        assert any(m == "POST" for m, h, p in calls)
        assert server.engine.store.convo("777")["stage"] != "new"


def test_check_meta_needs_admin_and_reports_hosts():
    with TestClient(server.app) as c:
        assert c.get("/check-meta").status_code == 401
        j = c.get("/check-meta?token=adm").json()
        assert j["meta_login"] == "unified" and len(j["hosts"]) == 2


def test_privacy_page():
    with TestClient(server.app) as c:
        r = c.get("/privacy")
        assert r.status_code == 200 and "Privacy Policy" in r.text and "delete my data" in r.text
        assert c.get("/data-deletion").status_code == 200


def _expiry_meta(expires_at, valid=True):
    sent = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.host, req.url.path))
        if req.url.path.endswith("/debug_token"):
            return httpx.Response(200, json={"data": {"is_valid": valid, "expires_at": expires_at}})
        if req.url.host == "hooks.example":
            sent.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={})
    return h, sent


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_facebook_host_token_warns_before_expiry(monkeypatch):
    import time
    h, sent = _expiry_meta(int(time.time()) + 5 * 86400)
    server.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    monkeypatch.setattr(server, "NOTIFY_URL", "https://hooks.example/x")
    server.engine.store.set_setting("graph_base", "https://graph.facebook.com/v23.0")
    server.engine.store.set_setting("ig_token_checked_on", "")
    _run(server.refresh_token_if_due())
    assert any("expires in" in t for t in sent), sent
    assert not any(p.endswith("refresh_access_token") for m, host, p in calls)
    sent.clear()
    _run(server.refresh_token_if_due())          # once a day, not every loop
    assert not sent


def test_facebook_host_token_quiet_when_far_or_never(monkeypatch):
    import time
    for exp in (0, int(time.time()) + 50 * 86400):
        h, sent = _expiry_meta(exp)
        server.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
        monkeypatch.setattr(server, "NOTIFY_URL", "https://hooks.example/x")
        server.engine.store.set_setting("graph_base", "https://graph.facebook.com/v23.0")
        server.engine.store.set_setting("ig_token_checked_on", "")
        _run(server.refresh_token_if_due())
        assert not sent


def test_instagram_host_token_is_renewed(monkeypatch):
    def h(req):
        calls.append((req.method, req.url.host, req.url.path))
        return httpx.Response(200, json={"access_token": "newtok", "expires_in": 5184000})
    server.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    server.engine.store.set_setting("graph_base", "https://graph.instagram.com/v23.0")
    server.engine.store.set_setting("ig_token_refreshed_at", "2026-01-01T00:00:00+00:00")
    _run(server.refresh_token_if_due())
    assert server.engine.store.get_setting("ig_token") == "newtok"
