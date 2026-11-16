"""Reinstalling an existing agent with an install token bound to it.

A bound token is the admin's proof that this host is that agent: enrolment
with it keeps the agent id and rotates its token, once, and only on the
agent's own hostname. See docs/specs/2026-11-16-agent-reinstall-design.md.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

import services.shared_state as ss

RAW_INSTALL = "install-token-for-tests"
RAW_BOUND = "nt_bound-token-for-tests"


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


async def _seed_token(sf, raw, *, agent_id=None, expires_in=timedelta(hours=1), revoked=False):
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _hash_install_token
    async with sf() as db:
        db.add(AgentInstallToken(token_hash=_hash_install_token(raw), prefix=raw[:8],
                                 expires_at=datetime.utcnow() + expires_in, revoked=revoked,
                                 agent_id=agent_id))
        await db.commit()


async def _enroll(client, key, hostname="web01", platform="linux"):
    return await client.post("/api/agent/enroll", json={
        "enrollment_key": key, "hostname": hostname, "platform": platform, "arch": "x86_64"})


async def _first_agent(client, sf):
    """Enrol web01 the normal way; returns the enrol response body."""
    await _seed_token(sf, RAW_INSTALL)
    resp = await _enroll(client, RAW_INSTALL)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _agent_count(sf):
    from models.agent import Agent
    async with sf() as db:
        return (await db.execute(select(func.count()).select_from(Agent))).scalar()


async def test_bound_token_reenrolls_same_id(auth_client):
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])

    resp = await _enroll(client, RAW_BOUND, platform="Windows")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == first["agent_id"]
    assert body["token"] and body["token"] != first["token"]
    assert await _agent_count(sf) == 1

    from models.agent import Agent
    async with sf() as db:
        assert (await db.get(Agent, first["agent_id"])).platform == "Windows"


async def test_reinstall_invalidates_old_token(auth_client):
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    new = (await _enroll(client, RAW_BOUND)).json()

    old = await client.post("/api/agent/report", json={},
                            headers={"Authorization": f"Bearer {first['token']}"})
    assert old.status_code == 403  # the report endpoint's "Invalid or disabled token"
    fresh = await client.post("/api/agent/report", json={},
                              headers={"Authorization": f"Bearer {new['token']}"})
    assert fresh.status_code not in (401, 403)


async def test_bound_token_single_use(auth_client):
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    assert (await _enroll(client, RAW_BOUND)).status_code == 200
    second = await _enroll(client, RAW_BOUND)
    assert second.status_code == 403
    assert second.json() == {"error": "Invalid enrollment credential"}


async def test_bound_token_hostname_mismatch_not_consumed(auth_client):
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])

    wrong = await _enroll(client, RAW_BOUND, hostname="web02")
    assert wrong.status_code == 403
    assert await _agent_count(sf) == 1

    right = await _enroll(client, RAW_BOUND, hostname="WEB01")
    assert right.status_code == 200, right.text
    assert right.json()["agent_id"] == first["agent_id"]


@pytest.mark.parametrize("kwargs", [
    {"expires_in": timedelta(minutes=-1)},
    {"revoked": True},
])
async def test_bound_token_expired_or_revoked_refused(auth_client, kwargs):
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"], **kwargs)
    assert (await _enroll(client, RAW_BOUND)).status_code == 403


async def test_bound_token_for_deleted_agent_refused(auth_client):
    from models.agent import Agent
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    async with sf() as db:
        await db.delete(await db.get(Agent, first["agent_id"]))
        await db.commit()

    assert (await _enroll(client, RAW_BOUND)).status_code == 403
    assert await _agent_count(sf) == 0


async def test_bound_token_writes_audit(auth_client):
    from models.audit import AuditLog
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    assert (await _enroll(client, RAW_BOUND)).status_code == 200

    async with sf() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action == "agent.reinstalled"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_type == "agent" and rows[0].target_id == first["agent_id"]


# ── POST /api/agents/{id}/reinstall ──────────────────────────────────────────

@pytest.fixture
async def admin_pair():
    from tests.test_routers.conftest import make_client
    async with make_client() as pair:  # auth bypassed: the built-in admin
        yield pair


async def _seed_agent(sf, hostname="web01"):
    from models.agent import Agent
    async with sf() as db:
        agent = Agent(name=hostname, hostname=hostname, token="x" * 64, platform="Windows")
        db.add(agent)
        await db.commit()
        return agent.id


async def test_reinstall_route_admin_only():
    from tests.test_routers.conftest import make_client
    from tests.test_routers.test_role_enforcement import ReadonlyUser
    async with make_client(fake_user=ReadonlyUser()) as (ac, sf):
        agent_id = await _seed_agent(sf)
        resp = await ac.post(f"/api/agents/{agent_id}/reinstall", json={})
    assert resp.status_code == 403


async def test_reinstall_route_unknown_agent_404(admin_pair):
    client, _sf = admin_pair
    resp = await client.post("/api/agents/9999/reinstall", json={})
    assert resp.status_code == 404


async def test_reinstall_route_issues_bound_token(admin_pair):
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    resp = await client.post(f"/api/agents/{agent_id}/reinstall", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"ok", "id", "token", "prefix", "expires_at", "install_linux", "install_windows",
            "agent_id"} <= body.keys()
    assert body["agent_id"] == agent_id
    assert "/install/linux?token=nt_" in body["install_linux"]

    enrol = await _enroll(client, body["token"])
    assert enrol.status_code == 200, enrol.text
    assert enrol.json()["agent_id"] == agent_id


@pytest.mark.parametrize("ttl,hours", [(None, 1), (0, 1), (100, 24), (6, 6)])
async def test_reinstall_route_ttl_clamped(admin_pair, ttl, hours):
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    body = {} if ttl is None else {"ttl_hours": ttl}
    before = datetime.utcnow()
    token_id = (await client.post(f"/api/agents/{agent_id}/reinstall", json=body)).json()["id"]
    async with sf() as db:
        row = await db.get(AgentInstallToken, token_id)
    delta = row.expires_at - before
    assert timedelta(hours=hours) - timedelta(minutes=1) <= delta <= timedelta(hours=hours, minutes=1)
    assert row.agent_id == agent_id


async def test_reinstall_route_audit(admin_pair):
    import json
    from models.audit import AuditLog
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    await client.post(f"/api/agents/{agent_id}/reinstall", json={})
    async with sf() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action == "install_token.create"))).scalars().all()
    assert len(rows) == 1
    details = json.loads(rows[0].details)
    assert details["reinstall"] is True and details["agent_id"] == agent_id


async def test_install_token_list_shows_bound_agent(admin_pair):
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    await client.post(f"/api/agents/{agent_id}/reinstall", json={})
    rows = (await client.get("/api/agents/install-tokens")).json()
    assert [(r["agent_id"], r["agent_name"]) for r in rows] == [(agent_id, "web01")]


async def test_incident_detail_agent_id_for_agent_silent(admin_pair):
    from models.incident import Incident
    client, sf = admin_pair
    async with sf() as db:
        silent = Incident(rule="agent_silent", title="Agent silent: web01", host_ids_hash="ag:7")
        other = Incident(rule="host_down", title="web01 down", host_ids_hash="ag:7")
        db.add_all([silent, other])
        await db.commit()
        ids = silent.id, other.id
    assert (await client.get(f"/api/v1/incidents/{ids[0]}")).json()["agent_id"] == 7
    assert (await client.get(f"/api/v1/incidents/{ids[1]}")).json()["agent_id"] is None


# ── Review fixes ─────────────────────────────────────────────────────────────

async def test_bound_token_spent_by_a_concurrent_enrolment_is_refused(auth_client):
    """Two installers racing with one bound token: the second must lose even
    if it read the token before the first one committed."""
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _consume_install_token, _hash_install_token
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])

    async with sf() as racer:
        stale = (await racer.execute(select(AgentInstallToken).where(
            AgentInstallToken.token_hash == _hash_install_token(RAW_BOUND)))).scalar_one()
        assert stale.used_count == 0
        async with sf() as winner:
            ok, _reason, _row = await _consume_install_token(winner, RAW_BOUND, "web01")
            assert ok
            await winner.commit()
        ok, reason, _row = await _consume_install_token(racer, RAW_BOUND, "web01")
    assert not ok and reason == "spent"


async def test_bound_enrolment_leaves_last_seen_to_the_first_report(auth_client):
    from models.agent import Agent
    client, sf = auth_client
    first = await _first_agent(client, sf)
    async with sf() as db:
        agent = await db.get(Agent, first["agent_id"])
        agent.last_seen = datetime(2026, 3, 29, 18, 51)
        await db.commit()
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    assert (await _enroll(client, RAW_BOUND)).status_code == 200
    async with sf() as db:
        assert (await db.get(Agent, first["agent_id"])).last_seen == datetime(2026, 3, 29, 18, 51)


async def test_bound_token_audit_names_the_token(auth_client):
    import json
    from models.audit import AuditLog
    client, sf = auth_client
    first = await _first_agent(client, sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    assert (await _enroll(client, RAW_BOUND)).status_code == 200
    async with sf() as db:
        row = (await db.execute(
            select(AuditLog).where(AuditLog.action == "agent.reinstalled"))).scalar_one()
    assert json.loads(row.details)["token_prefix"] == RAW_BOUND[:8]


async def test_reinstall_route_returns_server_issue_time(admin_pair):
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    body = (await client.post(f"/api/agents/{agent_id}/reinstall", json={})).json()
    issued = datetime.fromisoformat(body["issued_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    assert abs((datetime.utcnow() - issued).total_seconds()) < 60


async def test_reinstall_route_refuses_a_disabled_agent(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    async with sf() as db:
        (await db.get(Agent, agent_id)).enabled = False
        await db.commit()
    resp = await client.post(f"/api/agents/{agent_id}/reinstall", json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "agent_disabled"


# ── Tenant isolation and support access ─────────────────────────────────────

async def _seed_tenant_b_agent(sf, hostname="web01"):
    """Tenant 2 with an agent of the same hostname; returns (id, token hash)."""
    from models.agent import Agent
    from models.tenant import Site, Tenant
    from services.tenancy import system_scope
    async with sf() as db:
        with system_scope("test: second tenant"):
            db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
            await db.flush()
            db.add(Site(id=2, tenant_id=2, name="B"))
            await db.flush()
            agent = Agent(tenant_id=2, site_id=2, name=hostname, hostname=hostname, token="b" * 64)
            db.add(agent)
            await db.commit()
            return agent.id, agent.token


async def test_bound_token_cannot_reach_another_tenants_agent(auth_client):
    """A token of tenant 1 whose agent_id names tenant 2's agent (a forged or
    stale binding) enrols nothing and leaves that agent untouched."""
    from models.agent import Agent
    from services.tenancy import system_scope
    from sqlalchemy.exc import IntegrityError
    client, sf = auth_client
    await _first_agent(client, sf)
    b_id, b_token = await _seed_tenant_b_agent(sf)
    try:
        await _seed_token(sf, RAW_BOUND, agent_id=b_id)
    except IntegrityError:
        # PostgreSQL: the composite tenant FK refuses the binding outright.
        pass
    else:
        # SQLite enforces no FKs here: the enrolment check must refuse it.
        assert (await _enroll(client, RAW_BOUND)).status_code == 403
    async with sf() as db:
        with system_scope("test: read tenant 2"):
            assert (await db.get(Agent, b_id)).token == b_token


async def test_bound_token_reinstalls_its_own_tenants_agent_only(auth_client):
    from models.agent import Agent
    from services.tenancy import system_scope
    client, sf = auth_client
    first = await _first_agent(client, sf)
    b_id, b_token = await _seed_tenant_b_agent(sf)
    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])

    resp = await _enroll(client, RAW_BOUND)
    assert resp.status_code == 200 and resp.json()["agent_id"] == first["agent_id"]
    async with sf() as db:
        with system_scope("test: read tenant 2"):
            assert (await db.get(Agent, b_id)).token == b_token


async def test_reinstall_route_404_for_another_tenants_agent(admin_pair):
    client, sf = admin_pair
    b_id, _ = await _seed_tenant_b_agent(sf)
    assert (await client.post(f"/api/agents/{b_id}/reinstall", json={})).status_code == 404


async def test_reinstall_route_refused_through_support_access(admin_pair, monkeypatch):
    from services import tenancy
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    monkeypatch.setattr(tenancy, "request_via_support", lambda request: True)
    resp = await client.post(f"/api/agents/{agent_id}/reinstall", json={})
    assert resp.status_code == 403 and resp.json()["code"] == "support_access"


async def test_silent_condition_clears_once_the_reinstalled_agent_reports(auth_client):
    from models.agent import Agent
    from services import agent_silence as silence
    client, sf = auth_client
    first = await _first_agent(client, sf)
    async with sf() as db:
        (await db.get(Agent, first["agent_id"])).last_seen = datetime.utcnow() - timedelta(days=200)
        await db.commit()
        assert f"ag:{first['agent_id']}" in await silence.silent_conditions(db, datetime.utcnow())

    await _seed_token(sf, RAW_BOUND, agent_id=first["agent_id"])
    new = (await _enroll(client, RAW_BOUND)).json()
    report = await client.post("/api/agent/report", json={"hostname": "web01"},
                               headers={"Authorization": f"Bearer {new['token']}"})
    assert report.status_code == 200, report.text
    async with sf() as db:
        assert f"ag:{first['agent_id']}" not in await silence.silent_conditions(db, datetime.utcnow())
