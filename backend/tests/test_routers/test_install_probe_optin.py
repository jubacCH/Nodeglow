"""Probe opt-in through the install command (sealed deploy credentials)."""
import json

import pytest
from sqlalchemy import select

import services.shared_state as ss

SEAL_RM = 'rm -f "$INSTALL_DIR/seal.key"'
ENABLE = "enable-deploy"


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


@pytest.fixture
async def admin_pair():
    from tests.test_routers.conftest import make_client
    async with make_client() as pair:
        yield pair


async def _token(sf, **kw):
    from routers.agents import issue_install_token
    async with sf() as db:
        row, raw = await issue_install_token(db, note="t", ttl_hours=1, created_by="admin", **kw)
        await db.commit()
        return row.id, raw


async def _seed_agent(sf, hostname="web01"):
    from models.agent import Agent
    async with sf() as db:
        agent = Agent(name=hostname, hostname=hostname, token="x" * 64, platform="Linux")
        db.add(agent)
        await db.commit()
        return agent.id


def test_render_probe_has_enable_deploy_before_service_start():
    from routers.agents import render_linux_installer
    script = render_linux_installer("http://ng", "nt_x", probe=True)
    assert ENABLE in script
    line = ('if [ -x "$INSTALL_DIR/nodeglow-agent" ]; then "$INSTALL_DIR/nodeglow-agent" '
            '--config "$CONFIG_FILE" enable-deploy || echo "  Warning: could not enable '
            'agent deploys on this probe"; else echo "  Warning: could not enable agent deploys '
            'on this probe"; fi')
    assert line in script
    assert script.index(line) < script.index("systemctl daemon-reload")
    assert script.index(line) > script.index('chmod 600 "$CONFIG_FILE"')


def test_render_default_has_no_enable_deploy():
    from routers.agents import render_linux_installer
    assert ENABLE not in render_linux_installer("http://ng", "nt_x")


def test_render_seal_key_removal_only_when_fresh():
    from routers.agents import render_linux_installer
    fresh = render_linux_installer("http://ng", "nt_x", fresh=True)
    bound = render_linux_installer("http://ng", "nt_x", fresh=False)
    assert SEAL_RM in fresh
    assert fresh.index(SEAL_RM) < fresh.index("systemctl daemon-reload")
    assert SEAL_RM not in bound


async def test_install_route_probe_token(admin_pair):
    client, sf = admin_pair
    _id, raw = await _token(sf, probe=True)
    text = (await client.get(f"/install/linux?token={raw}")).text
    assert ENABLE in text and SEAL_RM in text


async def test_install_route_plain_token(admin_pair):
    client, sf = admin_pair
    _id, raw = await _token(sf)
    text = (await client.get(f"/install/linux?token={raw}")).text
    assert ENABLE not in text and SEAL_RM in text


async def test_install_route_bound_token_keeps_seal_key(admin_pair):
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    _id, raw = await _token(sf, agent_id=agent_id, probe=True)
    text = (await client.get(f"/install/linux?token={raw}")).text
    assert SEAL_RM not in text and ENABLE in text


async def test_create_token_api_probe_flag_and_audit(admin_pair):
    from models.agent_install_token import AgentInstallToken
    from models.audit import AuditLog
    client, sf = admin_pair
    resp = await client.post("/api/agents/install-tokens", json={"probe": True})
    assert resp.status_code == 200, resp.text
    async with sf() as db:
        row = (await db.execute(select(AgentInstallToken))).scalars().one()
        assert row.probe is True
        audit = (await db.execute(select(AuditLog).where(
            AuditLog.action == "install_token.create"))).scalars().one()
        assert json.loads(audit.details)["probe"] is True
    plain = await client.post("/api/agents/install-tokens", json={})
    assert plain.status_code == 200
    async with sf() as db:
        rows = (await db.execute(select(AgentInstallToken).order_by(AgentInstallToken.id))
                ).scalars().all()
        assert [r.probe for r in rows] == [True, False]


async def test_reinstall_api_probe_flag(admin_pair):
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    resp = await client.post(f"/api/agents/{agent_id}/reinstall", json={"probe": True})
    assert resp.status_code == 200, resp.text
    async with sf() as db:
        assert (await db.execute(select(AgentInstallToken))).scalars().one().probe is True


async def _enroll(client, key, hostname="web01"):
    return await client.post("/api/agent/enroll", json={
        "enrollment_key": key, "hostname": hostname, "platform": "linux", "arch": "x86_64"})


async def _is_probe(sf, agent_id):
    from models.agent import Agent
    async with sf() as db:
        return (await db.get(Agent, agent_id)).is_probe


async def test_enrol_probe_token_makes_new_agent_a_probe(admin_pair):
    client, sf = admin_pair
    _id, raw = await _token(sf, probe=True)
    resp = await _enroll(client, raw)
    assert resp.status_code == 200, resp.text
    assert await _is_probe(sf, resp.json()["agent_id"]) is True


async def test_enrol_plain_token_is_not_a_probe(admin_pair):
    client, sf = admin_pair
    _id, raw = await _token(sf)
    resp = await _enroll(client, raw)
    assert await _is_probe(sf, resp.json()["agent_id"]) is False


async def test_bound_probe_token_sets_probe_on_existing_agent(admin_pair):
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    _id, raw = await _token(sf, agent_id=agent_id, probe=True)
    assert (await _enroll(client, raw)).status_code == 200
    assert await _is_probe(sf, agent_id) is True


async def test_bound_plain_token_never_clears_probe(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    agent_id = await _seed_agent(sf)
    async with sf() as db:
        (await db.get(Agent, agent_id)).is_probe = True
        await db.commit()
    _id, raw = await _token(sf, agent_id=agent_id)
    assert (await _enroll(client, raw)).status_code == 200
    assert await _is_probe(sf, agent_id) is True


async def test_reenrol_by_hostname_with_probe_token_sets_probe(admin_pair):
    client, sf = admin_pair
    _id, raw1 = await _token(sf)
    first = (await _enroll(client, raw1)).json()
    _id, raw2 = await _token(sf, probe=True)
    resp = await client.post("/api/agent/enroll", json={
        "enrollment_key": raw2, "hostname": "web01", "platform": "linux",
        "arch": "x86_64", "agent_token": first["token"]})
    assert resp.status_code == 200, resp.text
    assert await _is_probe(sf, first["agent_id"]) is True
