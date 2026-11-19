"""Deploying agents through a probe: routes, host keys, jobs (part C).

See docs/specs/2026-11-19-probe-agent-deploy-design.md.
"""
import json
from datetime import datetime, timedelta

import pytest

import services.shared_state as ss

import asyncssh

_KEY = asyncssh.generate_private_key("ssh-ed25519")
FP = _KEY.get_fingerprint("sha256")
PUB = _KEY.export_public_key("openssh").decode().strip()


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


async def _seed_probe(sf, *, enabled=True, credentials=("lab-root",), is_probe=True, name="probe1"):
    from models.agent import Agent
    async with sf() as db:
        probe = Agent(name=name, hostname=name, token=name.ljust(64, "x"), is_probe=is_probe,
                      deploy_capability=json.dumps({"enabled": enabled,
                                                    "credentials": list(credentials),
                                                    "arch": ["x86_64"]}))
        db.add(probe)
        await db.commit()
        return probe.id


async def _rows(sf, model):
    from sqlalchemy import select
    async with sf() as db:
        return (await db.execute(select(model).order_by(model.id))).scalars().all()


async def _payload(sf, cmd):
    from services import tenant_keys
    async with sf() as db:
        return json.loads(await tenant_keys.decrypt_secret(db, cmd.payload, tenant_id=cmd.tenant_id))


async def _answer_host_key(sf, probe_id, host="10.0.0.5", port=22, fingerprint=FP, public_key=PUB):
    """The probe answers the host-key question (delivered, then its result,
    shaped as the spec says: no address, the server knows what it asked)."""
    from models.agent import Agent
    from services import agent_commands
    async with sf() as db:
        probe = await db.get(Agent, probe_id)
        cmd = await agent_commands.enqueue(db, probe, "ssh_host_key", {"host": host, "port": port},
                                           created_by="admin", target=f"{host}:{port}")
        await agent_commands.deliver(db, probe)
        await agent_commands.apply_results(db, probe, [{"id": cmd.id, "status": "ok", "result": {
            "key_type": "ssh-ed25519", "fingerprint": fingerprint, "public_key": public_key}}])
        await db.commit()
        return cmd.id


def _deploy_body(**kw):
    body = {"target": {"kind": "host", "host": "10.0.0.5", "port": 22}, "credential": "lab-root"}
    body.update(kw)
    return body


# ── Host key question ───────────────────────────────────────────────────────

async def test_host_key_question_is_a_command(admin_pair):
    from models.agent_deploy import AgentCommand
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    resp = await client.post(f"/api/probes/{probe_id}/host-key", json={"host": "10.0.0.5", "port": 2222})
    assert resp.status_code == 202, resp.text
    (cmd,) = await _rows(sf, AgentCommand)
    assert resp.json()["command_id"] == cmd.id and cmd.op == "ssh_host_key"
    assert await _payload(sf, cmd) == {"host": "10.0.0.5", "port": 2222}


@pytest.mark.parametrize("probe_kw, code", [
    ({"is_probe": False}, "not_a_probe"),
    ({"enabled": False}, "deploy_not_enabled"),
])
async def test_only_opted_in_probes(admin_pair, probe_kw, code):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf, **probe_kw)
    resp = await client.post(f"/api/probes/{probe_id}/host-key", json={"host": "10.0.0.5"})
    assert resp.status_code == 409 and resp.json()["code"] == code
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body())
    assert resp.status_code == 409 and resp.json()["code"] == code


async def test_unknown_probe_is_404(admin_pair):
    client, _sf = admin_pair
    assert (await client.post("/api/probes/9999/host-key", json={"host": "h"})).status_code == 404


async def test_command_status_is_readable(admin_pair):
    from models.agent_deploy import AgentCommand
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    (cmd,) = await _rows(sf, AgentCommand)
    view = (await client.get(f"/api/probes/{probe_id}/commands/{cmd.id}")).json()
    assert view["status"] == "ok" and view["result"]["fingerprint"] == FP
    other = await _seed_probe(sf, name="probe2")
    assert (await client.get(f"/api/probes/{other}/commands/{cmd.id}")).status_code == 404


# ── Deploy ──────────────────────────────────────────────────────────────────

async def test_an_unknown_host_key_must_be_asked_first(admin_pair):
    from models.agent_deploy import AgentCommand
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body())
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"
    assert await _rows(sf, AgentCommand) == []


async def test_a_confirmed_key_must_match_what_the_probe_saw(admin_pair):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    resp = await client.post(f"/api/probes/{probe_id}/deploy",
                             json=_deploy_body(host_key_fingerprint="SHA256:something-else"))
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"


async def test_deploy_to_a_host(admin_pair):
    from models.agent_deploy import AgentCommand, AgentDeployJob, SshHostKey
    from models.agent_install_token import AgentInstallToken
    from models.audit import AuditLog
    from routers.agents import _hash_install_token
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    assert resp.status_code == 202, resp.text

    (key,) = await _rows(sf, SshHostKey)
    assert (key.probe_id, key.host, key.port, key.fingerprint, key.public_key) == (
        probe_id, "10.0.0.5", 22, FP, PUB)
    cmd = [c for c in await _rows(sf, AgentCommand) if c.op == "deploy_agent"][0]
    payload = await _payload(sf, cmd)
    raw = payload["enrollment_token"]
    assert payload == {"target": {"kind": "host", "host": "10.0.0.5", "port": 22},
                       "credential": "lab-root", "host_key_fingerprint": FP,
                       "enrollment_token": raw}
    (token,) = await _rows(sf, AgentInstallToken)
    assert token.token_hash == _hash_install_token(raw) and token.agent_id is None
    assert token.note == "probe deploy 10.0.0.5"
    (job,) = await _rows(sf, AgentDeployJob)
    assert (job.kind, job.target, job.probe_id, job.command_id, job.status) == (
        "probe_host", "10.0.0.5", probe_id, cmd.id, "queued")
    assert resp.json()["run_id"] == job.run_id
    audit = [r for r in await _rows(sf, AuditLog) if r.action == "agent.deploy"][0]
    assert f'"probe_id": {probe_id}' in audit.details and raw not in audit.details


async def test_a_stored_key_needs_no_confirmation(admin_pair):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    first = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    assert first.status_code == 202
    again = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body())
    assert again.status_code == 202, again.text


async def test_deploy_into_containers(admin_pair):
    from models.agent_deploy import AgentCommand, AgentDeployJob
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id, host="pve1")
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(
        target={"kind": "lxc", "host": "pve1", "vmids": [105, 107, 105]}, host_key_fingerprint=FP))
    assert resp.status_code == 202, resp.text
    jobs = await _rows(sf, AgentDeployJob)
    assert [(j.kind, j.target) for j in jobs] == [("probe_lxc", "105 on pve1"), ("probe_lxc", "107 on pve1")]
    cmds = [c for c in await _rows(sf, AgentCommand) if c.op == "deploy_agent"]
    targets = [(await _payload(sf, c))["target"] for c in cmds]
    assert targets == [{"kind": "lxc", "host": "pve1", "port": 22, "vmid": 105},
                       {"kind": "lxc", "host": "pve1", "port": 22, "vmid": 107}]
    assert len(await _rows(sf, AgentInstallToken)) == 2


@pytest.mark.parametrize("patch, code", [
    ({"credential": "nope"}, "unknown_credential"),
    ({"target": {"kind": "host", "host": "-oProxyCommand=x"}}, "invalid_host"),
    ({"target": {"kind": "host", "host": "h", "port": 0}}, "invalid_port"),
    ({"target": {"kind": "vm", "host": "h"}}, "invalid_target"),
    ({"target": {"kind": "lxc", "host": "h", "vmids": []}}, "invalid_target"),
    ({"target": {"kind": "lxc", "host": "h", "vmids": ["105; rm"]}}, "invalid_target"),
    ({"target": {"kind": "lxc", "host": "h", "vmids": list(range(100, 160))}}, "invalid_target"),
])
async def test_invalid_deploys_are_400(admin_pair, patch, code):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(**patch))
    assert resp.status_code == 400 and resp.json()["code"] == code


async def test_reinstall_through_a_probe_binds_the_token(admin_pair):
    from models.agent import Agent
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    async with sf() as db:
        agent = Agent(name="web01", hostname="web01", token="w" * 64)
        db.add(agent)
        await db.commit()
        agent_id = agent.id
    await _answer_host_key(sf, probe_id)
    resp = await client.post(f"/api/probes/{probe_id}/deploy",
                             json=_deploy_body(host_key_fingerprint=FP, agent_id=agent_id))
    assert resp.status_code == 202
    (token,) = await _rows(sf, AgentInstallToken)
    assert token.agent_id == agent_id
    several = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(
        agent_id=agent_id, target={"kind": "lxc", "host": "10.0.0.5", "vmids": [1, 2]}))
    assert several.status_code == 400 and several.json()["code"] == "one_target_for_reinstall"


async def test_the_run_follows_the_probe(admin_pair):
    from models.agent import Agent
    from models.agent_deploy import AgentCommand
    from services import agent_commands
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    run_id = (await client.post(f"/api/probes/{probe_id}/deploy",
                                json=_deploy_body(host_key_fingerprint=FP))).json()["run_id"]
    view = (await client.get(f"/api/agent-deploy/{run_id}")).json()
    assert view["done"] is False and view["jobs"][0]["status"] == "queued"

    async with sf() as db:
        probe = await db.get(Agent, probe_id)
        await agent_commands.deliver(db, probe)
        await db.commit()
    assert (await client.get(f"/api/agent-deploy/{run_id}")).json()["jobs"][0]["status"] == "running"

    # The probe goes away: the command expires and the job reads as failed.
    async with sf() as db:
        cmd = [c for c in (await db.execute(
            __import__("sqlalchemy").select(AgentCommand))).scalars() if c.op == "deploy_agent"][0]
        cmd.expires_at = datetime.utcnow() - agent_commands.RESULT_GRACE - timedelta(seconds=1)
        await db.commit()
    view = (await client.get(f"/api/agent-deploy/{run_id}")).json()
    assert view["done"] is True and view["jobs"][0]["status"] == "failed"
    assert "probe" in view["jobs"][0]["detail"]


async def test_probe_deploy_is_admin_only():
    from tests.test_routers.conftest import make_client
    from tests.test_routers.test_role_enforcement import ReadonlyUser
    async with make_client(fake_user=ReadonlyUser()) as (ac, sf):
        probe_id = await _seed_probe(sf)
        assert (await ac.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body())).status_code == 403
        assert (await ac.post(f"/api/probes/{probe_id}/host-key", json={"host": "h"})).status_code == 403


async def test_probe_deploy_refused_through_support_access(admin_pair, monkeypatch):
    from services import tenancy
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    monkeypatch.setattr(tenancy, "request_via_support", lambda request: True)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body())
    assert resp.status_code == 403 and resp.json()["code"] == "support_access"


async def test_the_server_gate_does_not_apply(admin_pair, monkeypatch):
    """NODEGLOW_SSH_DEPLOY is about the server opening connections; a probe
    deploy is the probe's (and its local opt-in's) business."""
    client, sf = admin_pair
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", "off")
    probe_id = await _seed_probe(sf)
    resp = await client.post(f"/api/probes/{probe_id}/host-key", json={"host": "10.0.0.5"})
    assert resp.status_code == 202



async def test_an_answer_for_another_address_does_not_confirm(admin_pair):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id, host="10.0.0.9")
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"


async def test_a_public_key_that_does_not_hash_to_the_fingerprint_is_refused(admin_pair):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode().strip()
    await _answer_host_key(sf, probe_id, public_key=other)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"


async def test_the_host_key_question_is_audited(admin_pair):
    from models.audit import AuditLog
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await client.post(f"/api/probes/{probe_id}/host-key", json={"host": "10.0.0.5"})
    rows = [r for r in await _rows(sf, AuditLog) if r.action == "agent.host_key_query"]
    assert len(rows) == 1 and "10.0.0.5" in rows[0].details


async def test_deploy_commands_live_long_enough_for_a_big_run(admin_pair):
    from models.agent_deploy import AgentCommand
    from services import agent_commands
    client, sf = admin_pair
    probe_id = await _seed_probe(sf)
    await _answer_host_key(sf, probe_id)
    await client.post(f"/api/probes/{probe_id}/deploy", json=_deploy_body(host_key_fingerprint=FP))
    cmd = [c for c in await _rows(sf, AgentCommand) if c.op == "deploy_agent"][0]
    assert cmd.expires_at - cmd.created_at == agent_commands.DEPLOY_TTL


async def test_the_agent_api_shows_what_a_probe_can_deploy(admin_pair):
    client, sf = admin_pair
    probe_id = await _seed_probe(sf, credentials=("lab-root", "pve"))
    detail = (await client.get(f"/api/v1/agents/{probe_id}")).json()
    assert detail["deploy"] == {
            "enabled": True, "credentials": ["lab-root", "pve"], "arch": ["x86_64"], "sealed": False,
            "seal": {"fingerprint": None, "reported_fingerprint": None, "state": "none"}}
    listed = {a["id"]: a for a in (await client.get("/api/v1/agents")).json()}
    assert listed[probe_id]["deploy"]["enabled"] is True
    plain = await _seed_probe(sf, is_probe=False, name="plain")
    assert (await client.get(f"/api/v1/agents/{plain}")).json()["deploy"] is None


# ── Sealed logins ───────────────────────────────────────────────────────────

import base64  # noqa: E402

from services import seal_keys  # noqa: E402

_SEAL_PUB = base64.b64encode(bytes(range(32))).decode()
_SEAL_PUB_B = base64.b64encode(bytes(range(1, 33))).decode()


def _envelope(pub=_SEAL_PUB):
    return {"v": 1, "suite": seal_keys.SUITE, "key": seal_keys.fingerprint(pub),
            "enc": base64.b64encode(b"e" * 32).decode(), "ct": base64.b64encode(b"ct").decode()}


async def _seed_sealed(sf, *, sealed=True, other_probe=False):
    """A probe with a seal key and one sealed login; returns (probe_id, row_id)."""
    from models.agent import Agent
    from models.probe_credential import ProbeCredential
    from services import tenant_keys
    cap = {"enabled": True, "credentials": [], "arch": ["x86_64"], "sealed": sealed}
    async with sf() as db:
        probe = Agent(name="sp", hostname="sp", token="sp".ljust(64, "x"), is_probe=True,
                      deploy_capability=json.dumps(cap), seal_public_key=_SEAL_PUB,
                      seal_key_reported=_SEAL_PUB)
        db.add(probe)
        await db.flush()
        owner = probe
        if other_probe:
            owner = Agent(name="sq", hostname="sq", token="sq".ljust(64, "x"), is_probe=True,
                          deploy_capability=json.dumps(cap), seal_public_key=_SEAL_PUB,
                          seal_key_reported=_SEAL_PUB)
            db.add(owner)
            await db.flush()
        row = ProbeCredential(
            probe_id=owner.id, name="lab", username="root", auth="password", hosts="[]",
            key_fingerprint=seal_keys.fingerprint(_SEAL_PUB),
            sealed=await tenant_keys.encrypt_secret(db, json.dumps(_envelope()),
                                                    tenant_id=probe.tenant_id))
        db.add(row)
        await db.commit()
        return probe.id, row.id


async def test_deploy_with_sealed_login_carries_envelope(admin_pair):
    from models.agent_deploy import AgentCommand
    from models.audit import AuditLog
    from models.probe_credential import ProbeCredential
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    await _answer_host_key(sf, probe_id)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5", "port": 22},
        "sealed_credential_id": cred_id, "host_key_fingerprint": FP})
    assert resp.status_code == 202, resp.text
    (cmd,) = [c for c in await _rows(sf, AgentCommand) if c.op == "deploy_agent"]
    payload = await _payload(sf, cmd)
    assert payload["sealed_credential"] == _envelope()
    assert "credential" not in payload
    assert cmd.credential_id == cred_id
    (row,) = await _rows(sf, ProbeCredential)
    assert row.last_used_at is not None
    audit = [a for a in await _rows(sf, AuditLog) if a.action == "agent.deploy"][0]
    details = json.loads(audit.details)
    assert details["sealed_credential"] == {"id": cred_id, "name": "lab"}
    assert "credential" not in details


async def test_host_key_with_sealed_login(admin_pair):
    from models.agent_deploy import AgentCommand
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    resp = await client.post(f"/api/probes/{probe_id}/host-key", json={
        "host": "10.0.0.5", "sealed_credential_id": cred_id})
    assert resp.status_code == 202, resp.text
    (cmd,) = await _rows(sf, AgentCommand)
    assert cmd.target == "10.0.0.5:22" and cmd.credential_id == cred_id
    assert await _payload(sf, cmd) == {"host": "10.0.0.5", "port": 22,
                                       "sealed_credential": _envelope()}


async def test_both_credentials_refused(admin_pair):
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    for extra in ({"credential": "lab-root", "sealed_credential_id": cred_id}, {}):
        body = {"target": {"kind": "host", "host": "10.0.0.5"}, **extra}
        resp = await client.post(f"/api/probes/{probe_id}/deploy", json=body)
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_credential"
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": True})
    assert resp.status_code == 400 and resp.json()["code"] == "invalid_credential"


async def test_stale_sealed_login_refused(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    async with sf() as db:
        probe = await db.get(Agent, probe_id)
        probe.seal_public_key = probe.seal_key_reported = _SEAL_PUB_B
        await db.commit()
    body = {"target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": cred_id}
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json=body)
    assert resp.status_code == 409 and resp.json()["code"] == "credential_stale"
    resp = await client.post(f"/api/probes/{probe_id}/host-key", json={
        "host": "10.0.0.5", "sealed_credential_id": cred_id})
    assert resp.status_code == 409 and resp.json()["code"] == "credential_stale"


async def test_sealed_refused_when_probe_did_not_opt_in(admin_pair):
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf, sealed=False)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": cred_id})
    assert resp.status_code == 409 and resp.json()["code"] == "sealed_not_enabled"


async def test_unknown_and_foreign_sealed_login_refused(admin_pair):
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf, other_probe=True)
    for cid in (cred_id, 9999):
        resp = await client.post(f"/api/probes/{probe_id}/host-key", json={
            "host": "10.0.0.5", "sealed_credential_id": cid})
        assert resp.status_code == 400 and resp.json()["code"] == "unknown_credential"


async def test_changed_seal_key_refused_on_both_routes(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    async with sf() as db:
        (await db.get(Agent, probe_id)).seal_key_reported = _SEAL_PUB_B
        await db.commit()
    for path, body in (("deploy", {"target": {"kind": "host", "host": "10.0.0.5"}}),
                       ("host-key", {"host": "10.0.0.5"})):
        resp = await client.post(f"/api/probes/{probe_id}/{path}",
                                 json={**body, "sealed_credential_id": cred_id})
        assert resp.status_code == 409 and resp.json()["code"] == "seal_key_changed", path


async def test_unreadable_envelope_is_credential_stale(admin_pair):
    from models.probe_credential import ProbeCredential
    from services import tenant_keys
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    for plain in ("not json", "[1, 2]"):
        async with sf() as db:
            row = await db.get(ProbeCredential, cred_id)
            row.sealed = await tenant_keys.encrypt_secret(db, plain, tenant_id=row.tenant_id)
            await db.commit()
        resp = await client.post(f"/api/probes/{probe_id}/host-key", json={
            "host": "10.0.0.5", "sealed_credential_id": cred_id})
        assert resp.status_code == 409 and resp.json()["code"] == "credential_stale", plain


async def test_string_sealed_id_is_invalid(admin_pair):
    client, sf = admin_pair
    probe_id, _ = await _seed_sealed(sf)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": "5"})
    assert resp.status_code == 400 and resp.json()["code"] == "invalid_credential"


async def test_another_probes_sealed_id_refused_on_deploy(admin_pair):
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf, other_probe=True)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": cred_id})
    assert resp.status_code == 400 and resp.json()["code"] == "unknown_credential"


async def test_sealed_deploy_failing_pinning_queues_nothing(admin_pair):
    from models.agent_deploy import AgentCommand
    from models.probe_credential import ProbeCredential
    client, sf = admin_pair
    probe_id, cred_id = await _seed_sealed(sf)
    resp = await client.post(f"/api/probes/{probe_id}/deploy", json={
        "target": {"kind": "host", "host": "10.0.0.5"}, "sealed_credential_id": cred_id})
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"
    assert await _rows(sf, AgentCommand) == []
    (row,) = await _rows(sf, ProbeCredential)
    assert row.last_used_at is None
