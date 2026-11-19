"""Sealed logins and seal-key trust/rotation routes (part C).

See docs/specs/2026-11-19-probe-sealed-credentials-design.md.
"""
import base64
import hashlib
import json
from datetime import datetime

import pytest

import services.shared_state as ss
from services import seal_keys

PUB_A = base64.b64encode(bytes(range(32))).decode()
PUB_B = base64.b64encode(bytes(range(1, 33))).decode()
FP_A, FP_B = seal_keys.fingerprint(PUB_A), seal_keys.fingerprint(PUB_B)


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


def envelope(fp=FP_A, **kw):
    env = {"v": 1, "suite": seal_keys.SUITE, "key": fp,
           "enc": base64.b64encode(b"e" * 32).decode(), "ct": base64.b64encode(b"secret-ct").decode()}
    env.update(kw)
    return env


def body(**kw):
    out = {"name": "lab", "username": "root", "auth": "password", "hosts": ["10.0.0.0/24"],
           "sealed": envelope()}
    out.update(kw)
    return out


async def _seed_probe(sf, *, sealed=True, key=PUB_A, reported=None, enabled=True, name="probe1",
                      credentials=(), tenant=None):
    from models.agent import Agent
    cap = {"enabled": enabled, "credentials": list(credentials), "arch": ["x86_64"], "sealed": sealed}
    async with sf() as db:
        probe = Agent(name=name, hostname=name, token=name.ljust(64, "x"), is_probe=True,
                      deploy_capability=json.dumps(cap), seal_public_key=key,
                      seal_key_reported=reported or key)
        db.add(probe)
        await db.commit()
        return probe.id


async def _rows(sf, model):
    from sqlalchemy import select
    async with sf() as db:
        return (await db.execute(select(model).order_by(model.id))).scalars().all()


async def _create(client, probe_id, **kw):
    return await client.post(f"/api/probes/{probe_id}/credentials", json=body(**kw))


# -- create / list --------------------------------------------------------

async def test_create_and_list_never_expose_the_envelope(admin_pair):
    from models.probe_credential import ProbeCredential
    from services import tenant_keys
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    resp = await _create(client, pid, hosts=[" 10.0.0.1 "])
    assert resp.status_code == 201, resp.text
    cred = resp.json()["credential"]
    assert cred["name"] == "lab" and cred["hosts"] == ["10.0.0.1"] and cred["stale"] is False
    assert cred["key_fingerprint"] == FP_A and cred["created_by"] == "admin"
    assert "sealed" not in cred
    listed = (await client.get(f"/api/probes/{pid}/credentials")).json()["credentials"]
    assert [c["id"] for c in listed] == [cred["id"]] and "sealed" not in listed[0]
    assert "secret-ct" not in resp.text and "ct" not in cred
    (row,) = await _rows(sf, ProbeCredential)
    assert "secret-ct" not in row.sealed
    async with sf() as db:
        stored = json.loads(await tenant_keys.decrypt_secret(db, row.sealed, tenant_id=row.tenant_id))
    assert stored == envelope()


@pytest.mark.parametrize("probe_kw, code", [
    ({"sealed": False}, "sealed_not_enabled"),
    ({"key": None, "reported": None}, "seal_key_missing"),
    ({"reported": PUB_B}, "seal_key_changed"),
])
async def test_create_refused_by_probe_state(admin_pair, probe_kw, code):
    client, sf = admin_pair
    pid = await _seed_probe(sf, **probe_kw)
    resp = await _create(client, pid)
    assert resp.status_code == 409 and resp.json()["code"] == code


async def test_probe_without_seal_key_refuses_create(admin_pair):
    """An older agent never reports a key: refuse cleanly, no crash."""
    from models.agent import Agent
    client, sf = admin_pair
    pid = await _seed_probe(sf, sealed=False, key=None)
    async with sf() as db:
        probe = await db.get(Agent, pid)
        probe.seal_key_reported = None
        probe.deploy_capability = json.dumps({"enabled": True, "credentials": [], "arch": []})
        await db.commit()
    resp = await _create(client, pid)
    assert resp.status_code == 409 and resp.json()["code"] in ("sealed_not_enabled", "seal_key_missing")
    # A probe that has sealing but no key yet:
    pid2 = await _seed_probe(sf, key=None, name="p2")
    async with sf() as db:
        (await db.get(Agent, pid2)).seal_key_reported = None
        await db.commit()
    resp = await _create(client, pid2)
    assert resp.status_code == 409 and resp.json()["code"] == "seal_key_missing"


async def test_not_a_probe_and_not_enabled(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    async with sf() as db:
        db.add(Agent(name="a", hostname="a", token="a" * 64, is_probe=False))
        await db.commit()
    assert (await _create(client, 1)).json()["code"] == "not_a_probe"
    pid = await _seed_probe(sf, enabled=False, name="off")
    assert (await _create(client, pid)).json()["code"] == "deploy_not_enabled"
    assert (await _create(client, 9999)).status_code == 404


@pytest.mark.parametrize("kw, code", [
    ({"name": "bad name"}, "invalid_name"),
    ({"name": ""}, "invalid_name"),
    ({"auth": "token"}, "invalid_auth"),
    ({"hosts": []}, "invalid_hosts"),
    ({"hosts": ["x"] * 65}, "invalid_hosts"),
    ({"hosts": [" "]}, "invalid_hosts"),
    ({"hosts": ["x" * 256]}, "invalid_hosts"),
    ({"hosts": "10.0.0.1"}, "invalid_hosts"),
    ({"sealed": None}, "invalid_envelope"),
    ({"sealed": envelope(enc=base64.b64encode(b"e" * 31).decode())}, "invalid_envelope"),
    ({"sealed": envelope(ct="")}, "invalid_envelope"),
    ({"sealed": envelope(ct="not base64!")}, "invalid_envelope"),
    ({"sealed": envelope(v=2)}, "invalid_envelope"),
    ({"sealed": envelope(suite="other")}, "invalid_envelope"),
    ({"sealed": envelope(fp="SHA256:short")}, "invalid_envelope"),
    ({"sealed": envelope(extra="x")}, "invalid_envelope"),
    ({"sealed": envelope(ct=base64.b64encode(b"c" * 40000).decode())}, "invalid_envelope"),
])
async def test_create_validation(admin_pair, kw, code):
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    resp = await _create(client, pid, **kw)
    assert resp.status_code == 400 and resp.json()["code"] == code


async def test_username_must_be_set(admin_pair):
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    assert (await _create(client, pid, username="")).status_code == 400
    assert (await _create(client, pid, username="u" * 256)).status_code == 400


async def test_wrong_key_is_refused(admin_pair):
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    resp = await _create(client, pid, sealed=envelope(fp=FP_B))
    assert resp.status_code == 409 and resp.json()["code"] == "wrong_key"


async def test_name_taken_by_row_or_probe_credential(admin_pair):
    client, sf = admin_pair
    pid = await _seed_probe(sf, credentials=("lab-root",))
    assert (await _create(client, pid)).status_code == 201
    resp = await _create(client, pid)
    assert resp.status_code == 409 and resp.json()["code"] == "name_taken"
    resp = await _create(client, pid, name="lab-root")
    assert resp.status_code == 409 and resp.json()["code"] == "name_taken"


async def test_create_is_audited_without_secrets(admin_pair):
    from models.audit import AuditLog
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    await _create(client, pid)
    logs = [a for a in await _rows(sf, AuditLog) if a.action == "probe.credential.create"]
    assert len(logs) == 1 and "secret-ct" not in (logs[0].details or "")


# -- delete -----------------------------------------------------------------

async def test_delete_expires_queued_commands_and_fails_jobs(admin_pair):
    from models.agent import Agent
    from models.agent_deploy import AgentCommand, AgentDeployJob
    from models.probe_credential import ProbeCredential
    from services import agent_commands
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    cid = (await _create(client, pid)).json()["credential"]["id"]
    async with sf() as db:
        probe = await db.get(Agent, pid)
        queued = await agent_commands.enqueue(db, probe, "deploy_agent", {"x": 1}, created_by="admin")
        other = await agent_commands.enqueue(db, probe, "deploy_agent", {"x": 2}, created_by="admin")
        queued.credential_id = cid
        db.add(AgentDeployJob(run_id="r", kind="probe_host", target="h", status="queued",
                              created_by="admin", probe_id=pid, command_id=queued.id))
        db.add(AgentDeployJob(run_id="r", kind="probe_host", target="h2", status="queued",
                              created_by="admin", probe_id=pid, command_id=other.id))
        await db.commit()
    resp = await client.delete(f"/api/probes/{pid}/credentials/{cid}")
    assert resp.status_code == 200
    assert await _rows(sf, ProbeCredential) == []
    cmds = await _rows(sf, AgentCommand)
    assert (cmds[0].status, cmds[0].payload) == ("expired", None) and cmds[1].status == "queued"
    jobs = await _rows(sf, AgentDeployJob)
    assert jobs[0].status == "failed" and "deleted before the probe picked up" in jobs[0].detail
    assert jobs[1].status == "queued"


async def test_delete_of_another_probes_row_is_404(admin_pair):
    from models.probe_credential import ProbeCredential
    client, sf = admin_pair
    p1 = await _seed_probe(sf)
    p2 = await _seed_probe(sf, name="probe2")
    cid = (await _create(client, p1)).json()["credential"]["id"]
    assert (await client.delete(f"/api/probes/{p2}/credentials/{cid}")).status_code == 404
    assert len(await _rows(sf, ProbeCredential)) == 1
    assert (await client.delete(f"/api/probes/{p1}/credentials/9999")).status_code == 404


# -- stale / trust / rotate ---------------------------------------------------

async def test_stale_after_key_change_and_trust(admin_pair):
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    await _create(client, pid)
    from models.agent import Agent
    async with sf() as db:
        (await db.get(Agent, pid)).seal_key_reported = PUB_B
        await db.commit()
    bad = await client.post(f"/api/probes/{pid}/seal-key/trust", json={"fingerprint": FP_A})
    assert bad.status_code == 409 and bad.json()["code"] == "fingerprint_mismatch"
    ok = await client.post(f"/api/probes/{pid}/seal-key/trust", json={"fingerprint": FP_B})
    assert ok.status_code == 200 and ok.json()["fingerprint"] == FP_B and ok.json()["state"] == "trusted"
    (cred,) = (await client.get(f"/api/probes/{pid}/credentials")).json()["credentials"]
    assert cred["stale"] is True
    async with sf() as db:
        probe = await db.get(Agent, pid)
        assert probe.seal_public_key == PUB_B and probe.seal_key_trusted_by == "admin"


async def test_trust_without_report_is_mismatch(admin_pair):
    from models.agent import Agent
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    async with sf() as db:
        (await db.get(Agent, pid)).seal_key_reported = None
        await db.commit()
    resp = await client.post(f"/api/probes/{pid}/seal-key/trust", json={"fingerprint": FP_A})
    assert resp.status_code == 409 and resp.json()["code"] == "fingerprint_mismatch"


async def test_rotate_queues_a_command_and_its_result_trusts_the_new_key(admin_pair):
    from models.agent import Agent
    from models.agent_deploy import AgentCommand
    from models.audit import AuditLog
    from services import agent_commands
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    await _create(client, pid)
    resp = await client.post(f"/api/probes/{pid}/seal-key/rotate")
    assert resp.status_code == 202
    cmd_id = resp.json()["command_id"]
    (cmd,) = await _rows(sf, AgentCommand)
    assert cmd.id == cmd_id and cmd.op == "rotate_seal_key" and cmd.target is None
    async with sf() as db:
        probe = await db.get(Agent, pid)
        (out,) = await agent_commands.deliver(db, probe)
        assert out["op"] == "rotate_seal_key" and out["payload"] == {}
        await agent_commands.apply_results(db, probe, [{"id": cmd_id, "status": "ok",
                                                        "result": {"public_key": PUB_B}}])
        await db.commit()
    async with sf() as db:
        probe = await db.get(Agent, pid)
        assert probe.seal_public_key == PUB_B and probe.seal_key_trusted_by == "admin"
        assert seal_keys.state(probe)["state"] == "trusted"
        stored = await db.get(AgentCommand, cmd_id)
        assert json.loads(stored.result) == {"fingerprint": FP_B}
    (cred,) = (await client.get(f"/api/probes/{pid}/credentials")).json()["credentials"]
    assert cred["stale"] is True
    actions = [a.action for a in await _rows(sf, AuditLog)]
    assert "probe.seal_key.rotate" in actions and "probe.seal_key.trust" in actions
    view = (await client.get(f"/api/probes/{pid}/commands/{cmd_id}")).json()
    assert PUB_B not in json.dumps(view)


async def test_rotate_with_invalid_key_fails_and_trusts_nothing(admin_pair):
    from models.agent import Agent
    from models.agent_deploy import AgentCommand
    from services import agent_commands
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    cmd_id = (await client.post(f"/api/probes/{pid}/seal-key/rotate")).json()["command_id"]
    async with sf() as db:
        probe = await db.get(Agent, pid)
        await agent_commands.deliver(db, probe)
        await agent_commands.apply_results(db, probe, [{"id": cmd_id, "status": "ok",
                                                        "result": {"public_key": "garbage"}}])
        await db.commit()
    async with sf() as db:
        assert (await db.get(Agent, pid)).seal_public_key == PUB_A
        assert (await db.get(AgentCommand, cmd_id)).status == "failed"


async def test_rotate_requires_sealed_capability(admin_pair):
    client, sf = admin_pair
    pid = await _seed_probe(sf, sealed=False)
    resp = await client.post(f"/api/probes/{pid}/seal-key/rotate")
    assert resp.status_code == 409 and resp.json()["code"] == "sealed_not_enabled"


# -- access ---------------------------------------------------------------------

async def test_envelope_validator_returns_a_clean_dict():
    from services.agent_commands import validate_envelope
    assert validate_envelope(envelope()) == envelope()
    assert validate_envelope("x") is None
    assert validate_envelope({**envelope(), "v": True}) is None


async def test_readonly_user_is_refused():
    from tests.test_routers.conftest import make_client
    from tests.test_routers.test_role_enforcement import ReadonlyUser
    async with make_client(fake_user=ReadonlyUser()) as (ac, sf):
        pid = await _seed_probe(sf)
        assert (await ac.get(f"/api/probes/{pid}/credentials")).status_code == 403
        assert (await ac.post(f"/api/probes/{pid}/credentials", json=body())).status_code == 403


@pytest.mark.parametrize("method, path, kw", [
    ("get", "credentials", {}),
    ("post", "credentials", {"json": body()}),
    ("delete", "credentials/1", {}),
    ("post", "seal-key/trust", {"json": {"fingerprint": FP_A}}),
    ("post", "seal-key/rotate", {}),
])
async def test_refused_through_support_access(admin_pair, monkeypatch, method, path, kw):
    from services import tenancy
    client, sf = admin_pair
    pid = await _seed_probe(sf)
    monkeypatch.setattr(tenancy, "request_via_support", lambda request: True)
    resp = await getattr(client, method)(f"/api/probes/{pid}/{path}", **kw)
    assert resp.status_code == 403 and resp.json()["code"] == "support_access"


async def test_another_tenants_probe_and_logins_are_not_reachable(admin_pair):
    from models.agent import Agent
    from models.probe_credential import ProbeCredential
    from models.tenant import Site, Tenant
    from services.tenancy import system_scope
    client, sf = admin_pair
    async with sf() as db:
        with system_scope("test: second tenant"):
            db.add(Tenant(id=2, provider_id=1, name="B", slug="b", status="active"))
            await db.flush()
            db.add(Site(id=2, tenant_id=2, name="B"))
            await db.flush()
            probe = Agent(tenant_id=2, site_id=2, name="pb", hostname="pb", token="b" * 64,
                          is_probe=True, seal_public_key=PUB_A, seal_key_reported=PUB_A,
                          deploy_capability=json.dumps({"enabled": True, "credentials": [],
                                                        "arch": [], "sealed": True}))
            db.add(probe)
            await db.flush()
            cred = ProbeCredential(tenant_id=2, probe_id=probe.id, name="theirs", username="u",
                                   auth="password", hosts="[]", key_fingerprint=FP_A, sealed="x")
            db.add(cred)
            await db.commit()
            pid, cid = probe.id, cred.id
    assert (await client.get(f"/api/probes/{pid}/credentials")).status_code == 404
    assert (await _create(client, pid)).status_code == 404
    assert (await client.delete(f"/api/probes/{pid}/credentials/{cid}")).status_code == 404
    assert (await client.post(f"/api/probes/{pid}/seal-key/rotate")).status_code == 404
    mine = await _seed_probe(sf)
    assert (await client.delete(f"/api/probes/{mine}/credentials/{cid}")).status_code == 404
    async with sf() as db:
        with system_scope("test: verify"):
            assert await db.get(ProbeCredential, cid) is not None
