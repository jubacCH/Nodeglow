"""Structured commands for a probe, delivered with its heartbeat (part C).

See docs/specs/2026-11-19-probe-agent-deploy-design.md.
"""
import json
from datetime import datetime, timedelta

import pytest

import services.shared_state as ss

TOKEN = "nt_" + "a1" * 24


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


async def _probe(client, sf, hostname="probe1", is_probe=True):
    """Enrol an agent and make it a probe; returns (agent id, bearer token)."""
    from models.agent import Agent
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _hash_install_token
    raw = f"install-{hostname}"
    async with sf() as db:
        db.add(AgentInstallToken(token_hash=_hash_install_token(raw), prefix=raw[:8],
                                 expires_at=datetime.utcnow() + timedelta(hours=1)))
        await db.commit()
    resp = await client.post("/api/agent/enroll", json={
        "enrollment_key": raw, "hostname": hostname, "platform": "linux", "arch": "x86_64"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    async with sf() as db:
        (await db.get(Agent, body["agent_id"])).is_probe = is_probe
        await db.commit()
    return body["agent_id"], body["token"]


async def _report(client, token, hostname="probe1", **extra):
    resp = await client.post("/api/agent/report", json={"hostname": hostname, **extra},
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _enqueue(sf, agent_id, op="deploy_agent", payload=None, **kw):
    from models.agent import Agent
    from services import agent_commands
    async with sf() as db:
        agent = await db.get(Agent, agent_id)
        cmd = await agent_commands.enqueue(db, agent, op, payload or {"enrollment_token": TOKEN},
                                           created_by="admin", **kw)
        await db.commit()
        return cmd.id


async def _command(sf, cmd_id):
    from models.agent_deploy import AgentCommand
    async with sf() as db:
        return await db.get(AgentCommand, cmd_id)


async def test_payload_is_encrypted_at_rest(auth_client):
    client, sf = auth_client
    probe_id, _ = await _probe(client, sf)
    cmd = await _command(sf, await _enqueue(sf, probe_id))
    assert cmd.status == "queued" and cmd.payload and TOKEN not in cmd.payload


async def test_heartbeat_delivers_once_and_clears_the_payload(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    first = await _report(client, token)
    assert first["commands"] == [{
        "id": cmd_id, "op": "deploy_agent", "payload": {"enrollment_token": TOKEN},
        "expires_at": first["commands"][0]["expires_at"]}]
    cmd = await _command(sf, cmd_id)
    assert cmd.status == "delivered" and cmd.payload is None and cmd.delivered_at
    assert (await _report(client, token))["commands"] == []


async def test_only_probes_get_commands(auth_client):
    from models.agent import Agent
    from services import agent_commands
    client, sf = auth_client
    agent_id, token = await _probe(client, sf, is_probe=False)
    async with sf() as db:
        with pytest.raises(ValueError):
            await agent_commands.enqueue(db, await db.get(Agent, agent_id), "deploy_agent", {},
                                         created_by="admin")
    assert "commands" not in await _report(client, token)


async def test_unknown_ops_are_refused(auth_client):
    from models.agent import Agent
    from services import agent_commands
    client, sf = auth_client
    probe_id, _ = await _probe(client, sf)
    async with sf() as db:
        with pytest.raises(ValueError):
            await agent_commands.enqueue(db, await db.get(Agent, probe_id), "run_shell", {},
                                         created_by="admin")


async def test_expired_commands_are_not_delivered(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id, ttl=timedelta(seconds=-1))
    assert (await _report(client, token))["commands"] == []
    cmd = await _command(sf, cmd_id)
    assert cmd.status == "expired" and cmd.payload is None


async def test_at_most_five_per_heartbeat(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    for _ in range(7):
        await _enqueue(sf, probe_id)
    assert len((await _report(client, token))["commands"]) == 5
    assert len((await _report(client, token))["commands"]) == 2


async def test_results_finish_the_command_redacted(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    await _report(client, token)
    await _report(client, token, command_results=[
        {"id": cmd_id, "status": "ok", "result": {"detail": f"enrolled with {TOKEN} ok"}}])
    cmd = await _command(sf, cmd_id)
    assert cmd.status == "ok" and cmd.finished_at
    assert TOKEN not in cmd.result and TOKEN[:8] + "…" in cmd.result


async def test_results_for_foreign_or_finished_commands_are_ignored(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    _other_id, other_token = await _probe(client, sf, hostname="probe2")
    cmd_id = await _enqueue(sf, probe_id)
    await _report(client, token)
    await _report(client, other_token, hostname="probe2",
                  command_results=[{"id": cmd_id, "status": "ok", "result": {}}])
    assert (await _command(sf, cmd_id)).status == "delivered"
    await _report(client, token, command_results=[{"id": cmd_id, "status": "failed",
                                                   "result": {"detail": "no"}}])
    await _report(client, token, command_results=[{"id": cmd_id, "status": "ok", "result": {}}])
    assert (await _command(sf, cmd_id)).status == "failed"
    await _report(client, token, command_results=[{"id": 99999, "status": "ok"}, "junk",
                                                  {"id": "x", "status": "ok"}])


async def test_a_queued_command_is_not_finished_by_a_result(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    # A result can only answer what was delivered.
    from services import agent_commands
    from models.agent import Agent
    async with sf() as db:
        await agent_commands.apply_results(db, await db.get(Agent, probe_id),
                                           [{"id": cmd_id, "status": "ok", "result": {}}])
        await db.commit()
    assert (await _command(sf, cmd_id)).status == "queued"


async def test_the_probe_reports_its_deploy_capability(auth_client):
    from models.agent import Agent
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    await _report(client, token, deploy={"enabled": True, "credentials": ["lab-root", "", 5, "x" * 300],
                                         "arch": ["x86_64"], "extra": "ignored"})
    async with sf() as db:
        cap = json.loads((await db.get(Agent, probe_id)).deploy_capability)
    assert cap == {"enabled": True, "credentials": ["lab-root"], "arch": ["x86_64"],
                   "sealed": False}
    await _report(client, token, deploy={"enabled": "yes"})
    async with sf() as db:
        cap = json.loads((await db.get(Agent, probe_id)).deploy_capability)
    assert cap["enabled"] is False


async def test_a_job_follows_its_command(auth_client):
    from models.agent_deploy import AgentDeployJob
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    async with sf() as db:
        job = AgentDeployJob(run_id="r1", kind="probe_host", target="10.0.0.5", status="queued",
                             probe_id=probe_id, command_id=cmd_id)
        db.add(job)
        await db.commit()
        job_id = job.id
    await _report(client, token)
    async with sf() as db:
        job = await db.get(AgentDeployJob, job_id)
        assert job.status == "running" and job.started_at
    await _report(client, token, command_results=[
        {"id": cmd_id, "status": "failed", "result": {"detail": f"uname: aarch64 {TOKEN}"}}])
    async with sf() as db:
        job = await db.get(AgentDeployJob, job_id)
    assert job.status == "failed" and "aarch64" in job.detail and TOKEN not in job.detail


def test_effective_status_expires_unanswered_commands():
    from models.agent_deploy import AgentCommand
    from services import agent_commands
    now = datetime.utcnow()
    queued = AgentCommand(status="queued", expires_at=now - timedelta(seconds=1))
    delivered = AgentCommand(status="delivered", expires_at=now - timedelta(minutes=5))
    lost = AgentCommand(status="delivered", expires_at=now - agent_commands.RESULT_GRACE
                        - timedelta(seconds=1))
    assert agent_commands.effective_status(queued, now) == "expired"
    assert agent_commands.effective_status(delivered, now) == "delivered"
    assert agent_commands.effective_status(lost, now) == "expired"



async def test_the_metrics_snapshot_keeps_no_command_results(auth_client):
    from unittest.mock import patch
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    await _report(client, token)
    stored = []

    async def capture(rows):
        stored.extend(rows)

    # A patch scoped to the call: restored before the client's own patches.
    with patch("services.clickhouse_client.insert_agent_metrics", capture):
        await _report(client, token, deploy={"enabled": True, "credentials": ["x"]},
                      command_results=[{"id": cmd_id, "status": "failed",
                                        "result": {"detail": TOKEN}}])
    assert stored and all(TOKEN not in r["data_json"] for r in stored)
    assert all("command_results" not in json.loads(r["data_json"]) for r in stored)


async def test_a_late_result_does_not_reverse_an_expired_command(auth_client):
    from models.agent_deploy import AgentDeployJob
    from services import agent_commands
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    cmd_id = await _enqueue(sf, probe_id)
    async with sf() as db:
        db.add(AgentDeployJob(run_id="r", kind="probe_host", target="h", status="queued",
                              probe_id=probe_id, command_id=cmd_id))
        await db.commit()
    await _report(client, token)
    async with sf() as db:
        cmd = await db.get(__import__("models.agent_deploy", fromlist=["AgentCommand"]).AgentCommand, cmd_id)
        cmd.expires_at = datetime.utcnow() - agent_commands.RESULT_GRACE - timedelta(seconds=1)
        await db.commit()
    await _report(client, token, command_results=[{"id": cmd_id, "status": "ok", "result": {}}])
    cmd = await _command(sf, cmd_id)
    assert cmd.status == "expired"
    async with sf() as db:
        job = (await db.execute(__import__("sqlalchemy").select(AgentDeployJob))).scalars().one()
    assert job.status == "failed"


async def test_an_undecryptable_payload_fails_only_its_command(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    bad = await _enqueue(sf, probe_id)
    good = await _enqueue(sf, probe_id)
    async with sf() as db:
        cmd = await db.get(__import__("models.agent_deploy", fromlist=["AgentCommand"]).AgentCommand, bad)
        cmd.payload = "v2:nope:garbage"
        await db.commit()
    resp = await _report(client, token)
    assert [c["id"] for c in resp["commands"]] == [good]
    assert (await _command(sf, bad)).status == "failed"
