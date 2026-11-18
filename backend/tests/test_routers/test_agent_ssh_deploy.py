"""Agent deploy over SSH: the installer, tokens, host keys, jobs and routes.

See docs/specs/2026-11-18-agent-ssh-deploy-design.md.
"""
from datetime import datetime, timedelta

import pytest

import services.shared_state as ss


@pytest.fixture(autouse=True)
def _reset_limits(monkeypatch):
    # The test app loads ee (multi-tenant build), where "auto" is off.
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", "on")
    ss.reset()
    yield
    ss.reset()


@pytest.fixture
async def admin_pair():
    from tests.test_routers.conftest import make_client
    async with make_client() as pair:  # auth bypassed: the built-in admin
        yield pair


# ── Installer and tokens shared with the install routes ──────────────────────

def test_render_linux_installer_embeds_server_and_token():
    from routers.agents import render_linux_installer
    script = render_linux_installer("http://ng.example:8000", "nt_abc123")
    assert script.startswith("#!/bin/bash")
    assert 'SERVER="http://ng.example:8000"' in script
    assert 'ENROLLMENT_KEY="nt_abc123"' in script


async def test_install_route_serves_the_rendered_installer(admin_pair):
    from routers.agents import issue_install_token, render_linux_installer
    client, sf = admin_pair
    async with sf() as db:
        _row, raw = await issue_install_token(db, note="t", ttl_hours=1, created_by="admin")
        await db.commit()
    resp = await client.get(f"/install/linux?token={raw}")
    assert resp.status_code == 200
    server = resp.text.split('SERVER="', 1)[1].split('"', 1)[0]
    assert resp.text == render_linux_installer(server, raw)


async def test_issue_install_token_stores_only_a_hash(admin_pair):
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _hash_install_token, issue_install_token
    _client, sf = admin_pair
    async with sf() as db:
        row, raw = await issue_install_token(db, note="ssh deploy web01", ttl_hours=1,
                                             created_by="admin", site_id=None, agent_id=None)
        await db.commit()
        stored = await db.get(AgentInstallToken, row.id)
    assert raw.startswith("nt_") and stored.prefix == raw[:8]
    assert stored.token_hash == _hash_install_token(raw) and raw not in stored.token_hash
    assert stored.note == "ssh deploy web01" and stored.created_by == "admin"
    left = stored.expires_at - datetime.utcnow()
    assert timedelta(minutes=59) < left <= timedelta(hours=1)


# ── POST /api/agent-deploy, GET /api/agent-deploy/{run_id} ──────────────────

from tests.test_services.test_ssh_deploy import ssh_server  # noqa: E402,F401 — fixture


async def _drain():
    from services import agent_deploy
    await agent_deploy.drain()


def _body(srv, **kw):
    body = {"host": "127.0.0.1", "port": srv["port"], "username": "root", "password": "pw"}
    body.update(kw)
    return body


async def _trust(client, srv):
    """Confirm the fake server's host key the way the UI does."""
    first = await client.post("/api/agent-deploy", json=_body(srv))
    assert first.status_code == 409 and first.json()["code"] == "host_key_unknown"
    return first.json()["fingerprint"]


async def _rows(sf, model):
    from sqlalchemy import select
    async with sf() as db:
        return (await db.execute(select(model))).scalars().all()


async def test_unknown_host_key_asks_for_confirmation_first(admin_pair, ssh_server):
    from models.agent_deploy import AgentDeployJob, SshHostKey
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server))
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "host_key_unknown"
    assert body["fingerprint"] == ssh_server["fingerprint"] and body["key_type"] == "ssh-ed25519"
    assert ssh_server["rec"].auth_attempts == 0
    assert await _rows(sf, SshHostKey) == [] and await _rows(sf, AgentDeployJob) == []
    assert await _rows(sf, AgentInstallToken) == []


async def test_a_wrong_confirmation_is_not_trusted(admin_pair, ssh_server):
    from models.agent_deploy import SshHostKey
    client, sf = admin_pair
    resp = await client.post("/api/agent-deploy",
                             json=_body(ssh_server, host_key_fingerprint="SHA256:not-it"))
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"
    assert await _rows(sf, SshHostKey) == []


async def test_confirmed_deploy_runs_the_installer(admin_pair, ssh_server):
    from models.agent_deploy import AgentDeployJob, SshHostKey
    from models.agent_install_token import AgentInstallToken
    from routers.agents import _hash_install_token
    client, sf = admin_pair
    fp = await _trust(client, ssh_server)
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server, host_key_fingerprint=fp))
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    await _drain()

    keys = await _rows(sf, SshHostKey)
    assert [(k.host, k.port, k.fingerprint) for k in keys] == [("127.0.0.1", ssh_server["port"], fp)]
    script = ssh_server["rec"].stdin["bash -s"]
    raw = script.split('ENROLLMENT_KEY="', 1)[1].split('"', 1)[0]
    tokens = await _rows(sf, AgentInstallToken)
    assert [t.token_hash for t in tokens] == [_hash_install_token(raw)]
    assert tokens[0].note == "ssh deploy 127.0.0.1" and tokens[0].agent_id is None

    view = (await client.get(f"/api/agent-deploy/{run_id}")).json()
    assert view["done"] is True
    (job,) = view["jobs"]
    assert job["status"] == "ok" and job["kind"] == "ssh" and job["target"] == "127.0.0.1"
    assert raw not in job["detail"] and raw[:8] + "…" in job["detail"]
    stored = (await _rows(sf, AgentDeployJob))[0]
    assert raw not in (stored.detail or "") and "pw" not in (stored.detail or "").split()


async def test_deploy_is_audited_without_secrets(admin_pair, ssh_server):
    from models.audit import AuditLog
    client, sf = admin_pair
    fp = await _trust(client, ssh_server)
    await client.post("/api/agent-deploy", json=_body(ssh_server, host_key_fingerprint=fp,
                                                      password="pw", sudo_password="sudo-x"))
    await _drain()
    rows = {r.action: r for r in await _rows(sf, AuditLog)}
    assert "agent.deploy" in rows and "ssh_host_key.trust" in rows
    details = rows["agent.deploy"].details
    assert '"target": "127.0.0.1"' in details and "sudo-x" not in details
    assert '"pw"' not in details


async def test_a_changed_host_key_fails_without_logging_in(admin_pair, ssh_server):
    import asyncssh
    from models.agent_deploy import SshHostKey
    client, sf = admin_pair
    other = asyncssh.generate_private_key("ssh-ed25519")
    async with sf() as db:
        db.add(SshHostKey(host="127.0.0.1", port=ssh_server["port"], key_type="ssh-ed25519",
                          public_key=other.export_public_key("openssh").decode().strip(),
                          fingerprint=other.get_fingerprint("sha256")))
        await db.commit()
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server))
    assert resp.status_code == 202
    await _drain()
    (job,) = (await client.get(f"/api/agent-deploy/{resp.json()['run_id']}")).json()["jobs"]
    assert job["status"] == "failed" and "host key" in job["detail"].lower()
    assert ssh_server["rec"].auth_attempts == 0


async def test_reinstall_uses_a_token_bound_to_the_agent(admin_pair, ssh_server):
    from models.agent import Agent
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    async with sf() as db:
        agent = Agent(name="web01", hostname="web01", token="x" * 64)
        db.add(agent)
        await db.commit()
        agent_id = agent.id
    fp = await _trust(client, ssh_server)
    resp = await client.post("/api/agent-deploy",
                             json=_body(ssh_server, host_key_fingerprint=fp, agent_id=agent_id))
    assert resp.status_code == 202
    await _drain()
    (token,) = await _rows(sf, AgentInstallToken)
    assert token.agent_id == agent_id
    (job,) = (await client.get(f"/api/agent-deploy/{resp.json()['run_id']}")).json()["jobs"]
    assert job["agent_id"] == agent_id and job["status"] == "ok"


@pytest.mark.parametrize("enabled, status", [(True, 202), (False, 409)])
async def test_reinstall_needs_an_enabled_agent(admin_pair, ssh_server, enabled, status):
    from models.agent import Agent
    client, sf = admin_pair
    async with sf() as db:
        agent = Agent(name="web01", hostname="web01", token="x" * 64, enabled=enabled)
        db.add(agent)
        await db.commit()
        agent_id = agent.id
    fp = await _trust(client, ssh_server)
    resp = await client.post("/api/agent-deploy",
                             json=_body(ssh_server, host_key_fingerprint=fp, agent_id=agent_id))
    assert resp.status_code == status
    await _drain()


async def test_reinstall_of_an_unknown_agent_is_404(admin_pair, ssh_server):
    client, _sf = admin_pair
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server, agent_id=9999))
    assert resp.status_code == 404


async def test_stored_ssh_credential(admin_pair, ssh_server):
    from models.credential import Credential
    from services.snmp import encrypt_credential
    client, sf = admin_pair
    async with sf() as db:
        cred = Credential(name="root", type="ssh", data_json=await encrypt_credential(
            db, {"username": "root", "password": "pw", "private_key": ""}))
        snmp = Credential(name="snmp", type="snmp_v2c", data_json=await encrypt_credential(
            db, {"community": "public"}))
        db.add_all([cred, snmp])
        await db.commit()
        cred_id, snmp_id = cred.id, snmp.id
    fp = await _trust(client, ssh_server)
    base = {"host": "127.0.0.1", "port": ssh_server["port"], "host_key_fingerprint": fp}
    ok = await client.post("/api/agent-deploy", json={**base, "credential_id": cred_id})
    assert ok.status_code == 202, ok.text
    await _drain()
    (job,) = (await client.get(f"/api/agent-deploy/{ok.json()['run_id']}")).json()["jobs"]
    assert job["status"] == "ok"
    wrong = await client.post("/api/agent-deploy", json={**base, "credential_id": snmp_id})
    assert wrong.status_code == 400
    missing = await client.post("/api/agent-deploy", json={**base, "credential_id": 9999})
    assert missing.status_code == 404


@pytest.mark.parametrize("patch", [
    {"host": ""}, {"host": "-oProxyCommand=x"}, {"host": "a b"}, {"port": 0}, {"port": 70000},
    {"username": ""}, {"password": "", "private_key": ""},
])
async def test_invalid_requests_are_400(admin_pair, ssh_server, patch):
    client, _sf = admin_pair
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server, **patch))
    assert resp.status_code == 400


async def test_deploy_is_admin_only(ssh_server):
    from tests.test_routers.conftest import make_client
    from tests.test_routers.test_role_enforcement import ReadonlyUser
    async with make_client(fake_user=ReadonlyUser()) as (ac, _sf):
        resp = await ac.post("/api/agent-deploy", json=_body(ssh_server))
    assert resp.status_code == 403


async def test_deploy_is_refused_through_support_access(admin_pair, ssh_server, monkeypatch):
    from services import tenancy
    client, _sf = admin_pair
    monkeypatch.setattr(tenancy, "request_via_support", lambda request: True)
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server))
    assert resp.status_code == 403 and resp.json()["code"] == "support_access"


@pytest.mark.parametrize("value, multi, enabled", [
    ("auto", False, True), ("auto", True, False), ("on", True, True), ("off", False, False),
])
def test_the_gate(monkeypatch, value, multi, enabled):
    from services import agent_deploy, agent_release
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", value)
    monkeypatch.setattr(agent_release, "multi_tenant_mode", lambda: multi)
    assert agent_deploy.deploy_enabled() is enabled


async def test_a_closed_gate_refuses_and_is_reported(admin_pair, ssh_server, monkeypatch):
    client, _sf = admin_pair
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", "off")
    resp = await client.post("/api/agent-deploy", json=_body(ssh_server))
    assert resp.status_code == 403 and resp.json()["code"] == "ssh_deploy_disabled"
    assert (await client.get("/api/enrollment-info")).json()["ssh_deploy"] is False
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", "on")
    assert (await client.get("/api/enrollment-info")).json()["ssh_deploy"] is True


async def test_unknown_run_is_404(admin_pair):
    client, _sf = admin_pair
    assert (await client.get("/api/agent-deploy/nope")).status_code == 404


async def test_a_job_left_running_reads_as_interrupted(admin_pair):
    from models.agent_deploy import AgentDeployJob
    client, sf = admin_pair
    async with sf() as db:
        db.add(AgentDeployJob(run_id="r1", kind="ssh", target="h", status="running",
                              created_at=datetime.utcnow() - timedelta(minutes=16)))
        await db.commit()
    view = (await client.get("/api/agent-deploy/r1")).json()
    assert view["done"] is True
    assert view["jobs"][0]["status"] == "failed" and "interrupted" in view["jobs"][0]["detail"]


# ── Host keys ────────────────────────────────────────────────────────────────

async def test_host_keys_can_be_listed_and_forgotten(admin_pair, ssh_server):
    from models.audit import AuditLog
    client, sf = admin_pair
    fp = await _trust(client, ssh_server)
    await client.post("/api/agent-deploy", json=_body(ssh_server, host_key_fingerprint=fp))
    await _drain()
    (key,) = (await client.get("/api/ssh-host-keys")).json()["keys"]
    assert key["fingerprint"] == fp and key["host"] == "127.0.0.1"
    resp = await client.delete(f"/api/ssh-host-keys/{key['id']}")
    assert resp.status_code == 200
    assert (await client.get("/api/ssh-host-keys")).json()["keys"] == []
    assert any(r.action == "ssh_host_key.delete" for r in await _rows(sf, AuditLog))
    assert (await client.delete(f"/api/ssh-host-keys/{key['id']}")).status_code == 404


# ── Proxmox: deploy into every running container ─────────────────────────────

PVE_RESOURCES = [
    {"type": "lxc", "status": "running", "vmid": 105, "name": "web"},
    {"type": "lxc", "status": "stopped", "vmid": 106, "name": "off"},
    {"type": "qemu", "status": "running", "vmid": 200, "name": "vm"},
    {"type": "lxc", "status": "running", "vmid": 107, "name": "db"},
]


async def _seed_pve(sf, monkeypatch, port, key_pem=""):
    from integrations.proxmox import ProxmoxAPI
    from services import integration as int_svc

    async def resources(self):
        return PVE_RESOURCES

    monkeypatch.setattr(ProxmoxAPI, "cluster_resources", resources)
    async with sf() as db:
        cfg = await int_svc.create_config(db, "proxmox", "pve", {
            "host": "https://127.0.0.1:8006", "token_id": "root@pam!ng", "token_secret": "s",
            "verify_ssl": False, "ssh_user": "root", "ssh_private_key": key_pem,
            "ssh_port": str(port),
        })
        return cfg.id


def _pem(srv):
    return srv["client_key"].export_private_key().decode()


async def test_proxmox_asks_to_confirm_the_node_key(admin_pair, ssh_server, monkeypatch):
    client, sf = admin_pair
    cfg_id = await _seed_pve(sf, monkeypatch, ssh_server["port"], _pem(ssh_server))
    resp = await client.post(f"/api/v1/integrations/proxmox/{cfg_id}/deploy-agent", json={})
    assert resp.status_code == 409 and resp.json()["code"] == "host_key_unknown"
    assert resp.json()["fingerprint"] == ssh_server["fingerprint"]


async def test_proxmox_deploys_every_running_container(admin_pair, ssh_server, monkeypatch):
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    cfg_id = await _seed_pve(sf, monkeypatch, ssh_server["port"], _pem(ssh_server))
    url = f"/api/v1/integrations/proxmox/{cfg_id}/deploy-agent"
    resp = await client.post(url, json={"host_key_fingerprint": ssh_server["fingerprint"]})
    assert resp.status_code == 202, resp.text
    assert resp.json()["mode"] == "ssh"
    await _drain()

    rec = ssh_server["rec"]
    assert sorted(rec.commands) == ["pct exec 105 -- bash -s", "pct exec 107 -- bash -s"]
    raws = {rec.stdin[c].split('ENROLLMENT_KEY="', 1)[1].split('"', 1)[0] for c in rec.commands}
    assert len(raws) == 2
    notes = sorted(t.note for t in await _rows(sf, AgentInstallToken))
    assert notes == ["proxmox deploy db", "proxmox deploy web"]

    view = (await client.get(f"/api/agent-deploy/{resp.json()['run_id']}")).json()
    assert view["done"] is True
    assert sorted((j["target"], j["kind"], j["status"]) for j in view["jobs"]) == [
        ("105 web", "proxmox_lxc", "ok"), ("107 db", "proxmox_lxc", "ok")]
    assert all(raw not in j["detail"] for j in view["jobs"] for raw in raws)


async def test_proxmox_without_a_key_returns_a_script_with_a_token(admin_pair, monkeypatch):
    from models.agent_deploy import AgentDeployJob
    from models.agent_install_token import AgentInstallToken
    client, sf = admin_pair
    cfg_id = await _seed_pve(sf, monkeypatch, 22)
    resp = await client.post(f"/api/v1/integrations/proxmox/{cfg_id}/deploy-agent", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "script"
    (token,) = await _rows(sf, AgentInstallToken)
    assert "/install/linux?token=" in body["manual_script"] and token.prefix in body["manual_script"]
    assert "105 107" in body["manual_script"]
    assert await _rows(sf, AgentDeployJob) == []


async def test_proxmox_with_the_gate_closed_returns_the_script(admin_pair, ssh_server, monkeypatch):
    client, sf = admin_pair
    cfg_id = await _seed_pve(sf, monkeypatch, ssh_server["port"], _pem(ssh_server))
    monkeypatch.setenv("NODEGLOW_SSH_DEPLOY", "off")
    resp = await client.post(f"/api/v1/integrations/proxmox/{cfg_id}/deploy-agent", json={})
    assert resp.status_code == 200 and resp.json()["mode"] == "script"
    assert ssh_server["rec"].commands == []
