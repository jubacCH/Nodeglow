"""A probe reports its sealing key with the heartbeat; the first one is trusted."""
import base64
import hashlib
import json

import pytest

import services.shared_state as ss
from services import seal_keys
from tests.test_routers.test_probe_commands import _probe, _report


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


def _key(byte: int) -> tuple[str, str]:
    raw = bytes([byte]) * 32
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return base64.b64encode(raw).decode(), fp


def _seal(pub: str, suite: str = seal_keys.SUITE):
    return {"enabled": True, "seal": {"suite": suite, "public_key": pub}}


async def _agent(sf, agent_id):
    from models.agent import Agent
    async with sf() as db:
        return await db.get(Agent, agent_id)


async def test_first_reported_key_is_trusted_and_audited(auth_client):
    from sqlalchemy import select
    from models.audit import AuditLog
    from routers.api_v1 import _deploy_view
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    pub, fp = _key(1)
    await _report(client, token, deploy=_seal(pub))
    agent = await _agent(sf, probe_id)
    assert agent.seal_public_key == pub
    assert agent.seal_key_trusted_at is not None and agent.seal_key_trusted_by is None
    async with sf() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.action == "probe.seal_key.trust"))).scalars().all()
    assert len(rows) == 1
    assert json.loads(rows[0].details) == {"fingerprint": fp, "automatic": True}
    view = _deploy_view(agent)["seal"]
    assert view == {"fingerprint": fp, "public_key": pub, "reported_fingerprint": fp, "state": "trusted"}


async def test_changed_key_is_reported_not_trusted(auth_client):
    from routers.api_v1 import _deploy_view
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    first, fp1 = _key(1)
    second, fp2 = _key(2)
    await _report(client, token, deploy=_seal(first))
    await _report(client, token, deploy=_seal(second))
    agent = await _agent(sf, probe_id)
    assert agent.seal_public_key == first
    assert _deploy_view(agent)["seal"] == {
        "fingerprint": fp1, "public_key": first, "reported_fingerprint": fp2, "state": "changed"}


async def test_garbage_seal_report_is_ignored(auth_client):
    client, sf = auth_client
    probe_id, token = await _probe(client, sf)
    good, _ = _key(1)
    await _report(client, token, deploy=_seal(good))
    garbage = [
        _seal("abc"),
        _seal(good, suite="other"),
        _seal(base64.b64encode(b"\x01" * 33).decode()),
        _seal("A" * 100_000),
        {"enabled": True, "seal": [1, 2]},
    ]
    for dep in garbage:
        await _report(client, token, deploy=dep)
        agent = await _agent(sf, probe_id)
        assert agent.seal_public_key == good
        assert agent.seal_key_reported == good


async def test_sealed_flag_only_when_true():
    from services.agent_commands import capability, sanitize_capability

    class A:
        deploy_capability = None

    assert capability(A())["sealed"] is False
    for val, want in ((True, True), ("true", False), (1, False)):
        A.deploy_capability = sanitize_capability({"enabled": True, "sealed": val})
        assert capability(A())["sealed"] is want


def test_parse_report_canonicalises():
    pub, _ = _key(3)
    assert seal_keys.parse_report({"suite": seal_keys.SUITE, "public_key": pub}) == pub
    assert seal_keys.parse_report("x") is None
    assert seal_keys.fingerprint("!!!") is None
