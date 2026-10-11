"""Auth binding (multi-tenancy phase 4) through the HTTP stack, real auth.

Two tenants; tenant B's rows carry a canary. Memberships are the source of
truth for the role; sessions carry an active tenant that is re-checked
against the memberships on every request; API keys, the WebSocket hub, the
syslog live tail, notifications and the audit log are all per tenant.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from sqlalchemy import select

from models import ApiKey, AuditLog, NotificationLog, PingHost, Site, Tenant
from models.settings import Session, User, _hash_token
from models.tenant import TenantMembership, TenantSetting
from services import tenancy
from services.tenancy import system_scope, tenant_scope

CANARY = "CANARY-B"
A, B = 1, 2
PW = "Corr3ct-Horse!"


async def _seed(sf) -> None:
    async with sf() as s:
        with system_scope("test: seed tenant B", audit=False):
            s.add(Tenant(id=B, provider_id=1, name=f"{CANARY}-tenant", slug="b", status="active"))
            await s.flush()
            s.add(Site(id=2, tenant_id=B, name="B"))
            await s.flush()  # PostgreSQL enforces the host's site FK
            s.add(PingHost(id=900, tenant_id=B, name=f"{CANARY}-host", hostname="10.9.9.9", site_id=2))
            s.add(TenantSetting(tenant_id=B, key="site_name", value=f"{CANARY}-name"))
            s.add(TenantSetting(tenant_id=B, key="telegram_chat_id", value=f"{CANARY}-chat"))
            s.add(NotificationLog(tenant_id=B, channel="telegram", title=f"{CANARY}-notif",
                                  message="m", severity="info", status="sent"))
            s.add(PingHost(id=100, tenant_id=A, name="a-host", hostname="10.0.0.1", site_id=1))
            await s.commit()


async def _user(sf, username: str, memberships: dict[int, str], *, legacy_role: str = "admin") -> int:
    async with sf() as s:
        u = User(username=username, role=legacy_role, auth_source="local",
                 password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode())
        s.add(u)
        await s.flush()
        for tid, role in memberships.items():
            s.add(TenantMembership(user_id=u.id, tenant_id=tid, role=role))
        await s.commit()
        return u.id


async def _session(sf, user_id: int, active_tenant_id: int | None) -> str:
    token = secrets.token_hex(32)
    async with sf() as s:
        s.add(Session(token=_hash_token(token), user_id=user_id, active_tenant_id=active_tenant_id,
                      expires_at=datetime.utcnow() + timedelta(days=1)))
        await s.commit()
    return token


async def _login(client, username):
    return await client.post("/api/auth/login", json={"username": username, "password": PW})


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from services import shared_state

    shared_state.reset()  # login / PATCH limits are process-global
    yield
    shared_state.reset()


@pytest.fixture
async def env(auth_client):
    client, sf = auth_client
    await _seed(sf)
    return client, sf


# ── role from the membership ────────────────────────────────────────────────

async def test_membership_role_wins_over_users_role(env):
    client, sf = env
    await _user(sf, "carol", {A: "readonly"}, legacy_role="admin")
    r = await _login(client, "carol")
    assert r.status_code == 200 and r.json()["user"]["role"] == "readonly"
    assert (await client.get("/api/auth/me")).json()["user"]["role"] == "readonly"
    assert (await client.post("/api/v1/hosts", json={"name": "x", "hostname": "x"})).status_code == 403
    assert (await client.get("/api/users")).status_code == 403


async def test_login_records_the_active_tenant(env):
    client, sf = env
    uid = await _user(sf, "dave", {A: "admin"})
    assert (await _login(client, "dave")).status_code == 200
    async with sf() as s:
        sess = (await s.execute(select(Session).where(Session.user_id == uid))).scalar_one()
    assert sess.active_tenant_id == A


async def test_user_without_membership_cannot_sign_in(env):
    client, sf = env
    uid = await _user(sf, "erin", {})
    r = await _login(client, "erin")
    assert r.status_code == 403
    assert "nodeglow_session" not in r.cookies
    async with sf() as s:
        assert (await s.execute(select(Session).where(Session.user_id == uid))).first() is None


async def test_session_bound_to_a_foreign_tenant_is_rejected(env):
    client, sf = env
    uid = await _user(sf, "frank", {A: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, B))
    for path in ("/api/v1/hosts", "/api/v2/dashboard", "/settings/json"):
        r = await client.get(path)
        assert r.status_code == 404, path
        assert CANARY not in r.text
    assert (await client.get("/api/auth/me")).status_code == 404


async def test_header_selects_only_own_tenants(env):
    client, sf = env
    uid = await _user(sf, "gina", {A: "admin", B: "readonly"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, A))
    a = await client.get("/api/v1/hosts")
    assert a.status_code == 200 and CANARY not in a.text and "a-host" in a.text
    b = await client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "2"})
    assert b.status_code == 200 and CANARY in b.text and "a-host" not in b.text
    # Role per tenant: readonly in B.
    r = await client.post("/api/v1/hosts", json={"name": "x", "hostname": "x"},
                          headers={"X-Nodeglow-Tenant": "2"})
    assert r.status_code == 403
    # Settings are per tenant too.
    sa = (await client.get("/settings/json")).json()
    assert CANARY not in json.dumps(sa)
    for bad in ("3", "abc", "0"):
        assert (await client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": bad})).status_code == 404


async def test_header_for_a_tenant_without_membership_is_404(env):
    client, sf = env
    uid = await _user(sf, "hank", {A: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, None))
    assert (await client.get("/api/v1/hosts")).status_code == 200  # the only membership
    r = await client.get("/api/v1/hosts", headers={"X-Nodeglow-Tenant": "2"})
    assert r.status_code == 404 and CANARY not in r.text


async def test_several_memberships_without_a_choice_is_404(env):
    client, sf = env
    uid = await _user(sf, "ivan", {A: "admin", B: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, None))
    assert (await client.get("/api/v1/hosts")).status_code == 404


async def test_suspended_tenant_is_not_accessible(env):
    client, sf = env
    uid = await _user(sf, "jane", {A: "admin", B: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, B))
    assert (await client.get("/api/v1/hosts")).status_code == 200
    async with sf() as s:
        (await s.get(Tenant, B)).status = "suspended"
        await s.commit()
    assert (await client.get("/api/v1/hosts")).status_code == 404


async def test_switching_tenant_needs_a_membership_and_is_audited(env):
    client, sf = env
    uid = await _user(sf, "kate", {A: "admin", B: "editor"})
    token = await _session(sf, uid, A)
    client.cookies.set("nodeglow_session", token)
    assert (await client.post("/api/auth/tenant", json={"tenant_id": 3})).status_code == 404
    r = await client.post("/api/auth/tenant", json={"tenant_id": B})
    assert r.status_code == 200 and r.json()["role"] == "editor"
    assert CANARY in (await client.get("/api/v1/hosts")).text
    async with sf() as s:
        assert (await s.get(Session, _hash_token(token))).active_tenant_id == B
        with system_scope("test", audit=False):
            rows = (await s.execute(select(AuditLog).where(AuditLog.action == "auth.tenant_switch"))).scalars().all()
    assert [(r.tenant_id, r.user_id, r.acting_via) for r in rows] == [(B, uid, "membership")]


async def test_tenant_switch_requires_login_and_csrf(env):
    client, sf = env
    assert (await client.post("/api/auth/tenant", json={"tenant_id": 1})).status_code == 401
    uid = await _user(sf, "liam", {A: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, A))
    r = await client.post("/api/auth/tenant", json={"tenant_id": 1},
                          headers={"x-csrf-token": "forged"})
    assert r.status_code == 403


# ── users API manages memberships ───────────────────────────────────────────

async def test_users_api_lists_and_changes_only_own_tenant(env):
    client, sf = env
    await _user(sf, "admin1", {A: "admin"})
    b_only = await _user(sf, f"{CANARY}-user", {B: "admin"})
    assert (await _login(client, "admin1")).status_code == 200
    listed = (await client.get("/api/users")).json()
    assert [u["username"] for u in listed] == ["admin1"]
    assert (await client.patch(f"/api/users/{b_only}", json={"role": "readonly"})).status_code == 404
    assert (await client.delete(f"/api/users/{b_only}")).status_code == 404

    r = await client.post("/api/users", json={"username": "newbie", "password": "Sup3r-Secret-pw!",
                                              "role": "editor"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    async with sf() as s:
        m = await s.get(TenantMembership, (new_id, A))
        assert m.role == "editor"
        assert (await s.get(User, new_id)).role == "editor"  # mirrored for rollback
    assert (await client.patch(f"/api/users/{new_id}", json={"role": "admin"})).status_code == 200
    async with sf() as s:
        assert (await s.get(TenantMembership, (new_id, A))).role == "admin"
        assert (await s.get(User, new_id)).role == "admin"
    assert (await client.delete(f"/api/users/{new_id}")).status_code == 200
    async with sf() as s:
        assert await s.get(User, new_id) is None
        assert await s.get(TenantMembership, (new_id, A)) is None
        # B's user is untouched.
        assert (await s.get(TenantMembership, (b_only, B))).role == "admin"


async def test_last_admin_is_counted_per_tenant(env):
    client, sf = env
    me = await _user(sf, "solo", {A: "admin"})
    await _user(sf, "other-admin", {B: "admin"})  # an admin elsewhere does not count
    assert (await _login(client, "solo")).status_code == 200
    r = await client.patch(f"/api/users/{me}", json={"role": "readonly"})
    assert r.status_code == 400 and "last admin" in r.text


# ── API keys ────────────────────────────────────────────────────────────────

async def _api_key(sf, tenant_id: int, role: str = "admin") -> str:
    from routers.api_v1 import _hash_key

    raw = f"ng_{secrets.token_hex(12)}"
    async with sf() as s:
        with tenant_scope(tenant_id):
            s.add(ApiKey(name=f"key-{tenant_id}", key_hash=_hash_key(raw), prefix=raw[:8],
                         role=role, enabled=True, hash_algo="hmac"))
            await s.commit()
    return raw


async def test_api_key_of_a_sees_nothing_of_b(env):
    client, sf = env
    key_a = await _api_key(sf, A)
    key_b = await _api_key(sf, B)
    r = await client.get("/api/v1/hosts", headers={"X-API-Key": key_a})
    assert r.status_code == 200 and CANARY not in r.text and "a-host" in r.text
    assert (await client.get("/api/v1/hosts/900", headers={"X-API-Key": key_a})).status_code == 404
    r = await client.get("/api/v1/keys", headers={"X-API-Key": key_a})
    assert r.status_code == 200 and "key-2" not in r.text
    # The key decides; a header naming another tenant is refused.
    r = await client.get("/api/v1/hosts", headers={"X-API-Key": key_a, "X-Nodeglow-Tenant": "2"})
    assert r.status_code == 404
    r = await client.get("/api/v1/hosts", headers={"X-API-Key": key_b})
    assert r.status_code == 200 and CANARY in r.text and "a-host" not in r.text


async def test_api_key_of_a_suspended_tenant_stops_working(env):
    client, sf = env
    key_b = await _api_key(sf, B)
    async with sf() as s:
        (await s.get(Tenant, B)).status = "suspended"
        await s.commit()
    assert (await client.get("/api/v1/hosts", headers={"X-API-Key": key_b})).status_code == 401


# ── audit ───────────────────────────────────────────────────────────────────

async def test_audit_rows_carry_tenant_provider_and_how(env):
    client, sf = env
    uid = await _user(sf, "mona", {A: "admin", B: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, A))
    key_b = await _api_key(sf, B)
    r = await client.post("/api/v1/hosts", json={"name": "via-session", "hostname": "10.1.1.1"},
                          headers={"X-Nodeglow-Tenant": "2"})
    assert r.status_code in (200, 201), r.text
    client.cookies.clear()
    r = await client.post("/api/v1/hosts", json={"name": "via-key", "hostname": "10.1.1.2"},
                          headers={"X-API-Key": key_b})
    assert r.status_code in (200, 201), r.text
    async with sf() as s:
        with system_scope("test", audit=False):
            rows = (await s.execute(
                select(AuditLog).where(AuditLog.action.like("host.%")).order_by(AuditLog.id)
            )).scalars().all()
    assert [(r.tenant_id, r.provider_id, r.acting_via) for r in rows] == [
        (B, 1, "membership"), (B, 1, "api_key"),
    ]


async def test_system_scope_writes_are_audited_reads_are_not(db):
    from models.log_template import LogTemplate

    with system_scope("test: read only"):
        (await db.execute(select(PingHost))).all()
        await db.commit()
    with system_scope("test: repair templates"):
        db.add(LogTemplate(tenant_id=A, template_hash="h", template="t"))
        await db.commit()
    with system_scope("test", audit=False):
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert [(r.action, r.tenant_id, r.target_name, r.acting_via) for r in rows] == [
        ("system_scope.write", A, "test: repair templates", "system"),
    ]
    assert json.loads(rows[0].details)["tables"] == ["log_templates"]


# ── WebSocket and SSE fan-out ───────────────────────────────────────────────

class _FakeWs:
    def __init__(self):
        self.sent: list[str] = []

    async def accept(self):
        pass

    async def send_text(self, msg):
        self.sent.append(msg)


async def test_websocket_events_reach_only_their_tenant():
    from services import websocket as hub

    wa, wb, wab = _FakeWs(), _FakeWs(), _FakeWs()
    await hub.register(wa, role="admin", tenant_id=A)
    await hub.register(wb, role="admin", tenant_id=B)
    await hub.register(wab, role="readonly", tenant_ids={A, B})  # an ee portal view
    try:
        await hub.broadcast_ping_update(900, f"{CANARY}-host", False, None, tenant_id=B)
        await hub.broadcast("agent_metric", {"agent_name": "a"}, tenant_id=A)
        await hub.broadcast("admin_only", {"x": 1}, tenant_id=B, min_role="admin")
        assert not any(CANARY in m for m in wa.sent) and len(wa.sent) == 1
        assert [json.loads(m)["type"] for m in wb.sent] == ["ping_update", "admin_only"]
        assert [json.loads(m)["type"] for m in wab.sent] == ["ping_update", "agent_metric"]
        with pytest.raises(TypeError):
            await hub.broadcast("x", {})  # no tenant: refused, not "everyone"
    finally:
        for w in (wa, wb, wab):
            hub.unregister(w)


async def test_websocket_connect_resolves_tenant_like_http(env):
    _, sf = env
    uid = await _user(sf, "nina", {A: "admin"})

    class _WsReq:
        def __init__(self, query):
            self.headers = {}
            self.query_params = query
            self.scope = {"type": "websocket"}
            self.state = type("S", (), {})()
            self.state.session_active_tenant_id = None

    async with sf() as s:
        user = await s.get(User, uid)
    backend = tenancy.backend()
    assert (await backend.resolve_access(_WsReq({}), user)).tenant_id == A
    assert await backend.resolve_access(_WsReq({"tenant": "2"}), user) is None
    assert (await backend.resolve_access(_WsReq({"tenant": "1"}), user)).role == "admin"


# ── notifications ───────────────────────────────────────────────────────────

async def test_notifications_use_the_tenants_channels_and_cooldown(env):
    import notifications
    from tests.conftest import put_settings

    _, sf = env
    async with sf() as s:
        await put_settings(s, tenant_id=A, notify_enabled="1", telegram_bot_token="tok-a",
                           telegram_chat_id="chat-a")
        await put_settings(s, tenant_id=B, notify_enabled="1", telegram_bot_token="tok-b",
                           telegram_chat_id="chat-b")
    notifications._recent.clear()
    sent = AsyncMock()
    with patch.object(notifications, "_send_telegram", sent):
        await notifications.notify("Host down: router", "m", tenant_id=A)
        await notifications.notify("Host down: router", "m", tenant_id=B)  # not rate-limited by A
        await notifications.notify("Host down: router", "m", tenant_id=B)  # rate-limited
        with tenant_scope(A):
            await notifications.notify("Other", "m")  # current tenant
    assert [c.args[:2] for c in sent.await_args_list] == [
        ("tok-a", "chat-a"), ("tok-b", "chat-b"), ("tok-a", "chat-a"),
    ]
    async with sf() as s:
        with system_scope("test", audit=False):
            logs = (await s.execute(
                select(NotificationLog.tenant_id, NotificationLog.title)
                .where(NotificationLog.title != f"{CANARY}-notif").order_by(NotificationLog.id)
            )).all()
    assert [tuple(r) for r in logs] == [(A, "Host down: router"), (B, "Host down: router"), (A, "Other")]
    with pytest.raises(tenancy.TenantContextError):
        await notifications.notify("no tenant", "m")
    notifications._recent.clear()


async def test_notification_history_is_per_tenant(env):
    client, sf = env
    uid = await _user(sf, "olga", {A: "admin", B: "admin"})
    client.cookies.set("nodeglow_session", await _session(sf, uid, A))
    assert CANARY not in (await client.get("/settings/notifications/history")).text
    r = await client.get("/settings/notifications/history", headers={"X-Nodeglow-Tenant": "2"})
    assert CANARY in r.text
