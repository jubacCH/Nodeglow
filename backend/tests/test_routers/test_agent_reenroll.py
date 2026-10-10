"""Re-enrolling a known hostname requires that agent's current token.

``/api/agent/enroll`` with an existing hostname rotated the token and returned
the new one to the caller. Anyone holding an install token could therefore
take over any enrolled agent just by naming its hostname — and the real agent
was left with a dead token.
"""
from datetime import datetime, timedelta

import pytest

import services.shared_state as ss


RAW_INSTALL = "install-token-for-tests"


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


async def _seed_install_token(sf):
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _hash_install_token
    async with sf() as db:
        db.add(AgentInstallToken(token_hash=_hash_install_token(RAW_INSTALL), prefix="it",
                                 expires_at=datetime.utcnow() + timedelta(hours=1)))
        await db.commit()


async def _enroll(client, hostname="web01", **extra):
    body = {"enrollment_key": RAW_INSTALL, "hostname": hostname, "platform": "linux", "arch": "x86_64"}
    body.update(extra.pop("body", {}))
    return await client.post("/api/agent/enroll", json=body, **extra)


async def _agent_hash(sf, agent_id):
    from models.agent import Agent
    async with sf() as db:
        return (await db.get(Agent, agent_id)).token


async def test_first_enrollment_works(auth_client):
    client, sf = auth_client
    await _seed_install_token(sf)
    resp = await _enroll(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["token"]


async def test_reenroll_without_current_token_is_refused(auth_client):
    client, sf = auth_client
    await _seed_install_token(sf)
    first = (await _enroll(client)).json()
    before = await _agent_hash(sf, first["agent_id"])

    resp = await _enroll(client, hostname="WEB01")  # case-insensitive match
    assert resp.status_code == 409
    assert resp.json()["code"] == "agent_exists"
    assert "token" not in resp.json()
    assert await _agent_hash(sf, first["agent_id"]) == before  # not rotated

    resp = await _enroll(client, body={"agent_token": "deadbeef" * 6})
    assert resp.status_code == 409


async def test_reenroll_with_current_token_rotates(auth_client):
    client, sf = auth_client
    await _seed_install_token(sf)
    first = (await _enroll(client)).json()

    resp = await _enroll(client, body={"agent_token": first["token"]})
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["agent_id"] == first["agent_id"]
    assert second["token"] != first["token"]

    # Bearer header works too (with the newly rotated token).
    resp = await _enroll(client, headers={"Authorization": f"Bearer {second['token']}"})
    assert resp.status_code == 200, resp.text


async def test_after_admin_deletes_agent_host_enrolls_fresh(auth_client):
    from models.agent import Agent
    client, sf = auth_client
    await _seed_install_token(sf)
    first = (await _enroll(client)).json()
    async with sf() as db:
        await db.delete(await db.get(Agent, first["agent_id"]))
        await db.commit()
    resp = await _enroll(client)
    assert resp.status_code == 200, resp.text


async def test_installers_send_the_existing_token(auth_client):
    client, sf = auth_client
    await _seed_install_token(sf)
    for platform in ("linux", "windows"):
        resp = await client.get(f"/install/{platform}", params={"token": RAW_INSTALL})
        assert resp.status_code == 200, resp.text
        assert "agent_token" in resp.text, platform
