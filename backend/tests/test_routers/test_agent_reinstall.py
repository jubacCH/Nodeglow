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
