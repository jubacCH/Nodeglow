"""Router tests for the signed agent-update endpoints (server key + release key)."""
import hashlib
import json
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import routers.agents as agents
from services import agent_release, agent_signing

RAW_INSTALL = "install-token-for-signing-tests"
RAW_AGENT = "agent-token-for-signing-tests"


@pytest.fixture(autouse=True)
def _fresh_key():
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()
    yield
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()


@pytest.fixture
def single_tenant(monkeypatch):
    monkeypatch.setattr(agent_release, "multi_tenant_mode", lambda: False)


@pytest.fixture
def multi_tenant(monkeypatch):
    monkeypatch.setattr(agent_release, "multi_tenant_mode", lambda: True)


@pytest.fixture
def binary(tmp_path, monkeypatch):
    path = tmp_path / "nodeglow-agent-linux"
    path.write_bytes(b"\x7fELF" + b"a real-ish agent binary" * 50)
    monkeypatch.setattr(agents, "_agent_binary_path", lambda platform: path)
    agents._agent_sig_cache.clear()
    agents._agent_file_cache.clear()
    yield path
    agents._agent_sig_cache.clear()
    agents._agent_file_cache.clear()


def _release_sign(path, platform="linux", key=None, sha=None):
    """What CI does (tools/agent-release-key/sign.sh), with a throwaway key."""
    key = key or Ed25519PrivateKey.generate()
    data_sha = sha or hashlib.sha256(path.read_bytes()).hexdigest()
    pub = key.public_key().public_bytes_raw()
    doc = {
        "format": agent_release.SIGNATURE_FORMAT, "alg": "ed25519", "platform": platform,
        "key_id": agent_release.key_id(pub), "sha256": data_sha,
        "signature": key.sign(agent_release.release_message(platform, data_sha)).hex(),
    }
    agent_release.signature_path(path).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    return key, doc


# ── server-held key (pre-P2a path, single-tenant) ────────────────────────────

async def test_update_public_key_endpoint(client, single_tenant):
    resp = await client.get("/api/agent/update-public-key")
    assert resp.status_code == 200
    pub = resp.json()["public_key"]
    assert len(bytes.fromhex(pub)) == 32


async def test_update_public_key_is_not_handed_out_in_multi_tenant_mode(client, multi_tenant):
    resp = await client.get("/api/agent/update-public-key")
    assert resp.status_code == 404


async def test_version_endpoint_shape(client, single_tenant):
    resp = await client.get("/api/agent/version/linux")
    assert resp.status_code == 200
    body = resp.json()
    for field in ("hash", "signature", "release_signature", "release_key_id", "multi_tenant"):
        assert field in body
    assert body["multi_tenant"] is False


async def test_version_signature_verifies_against_binary(client, binary, single_tenant):
    """The server-key signature must verify with the server public key."""
    ver = (await client.get("/api/agent/version/linux")).json()
    pub_hex = (await client.get("/api/agent/update-public-key")).json()["public_key"]

    assert ver["hash"] and ver["signature"]
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    # Verifies over the exact served bytes — the check the Rust agent performs.
    pub.verify(bytes.fromhex(ver["signature"]), binary.read_bytes())


async def test_invalid_platform_rejected(client):
    resp = await client.get("/api/agent/version/bogus")
    assert resp.status_code == 400


# ── release signature (CI) ───────────────────────────────────────────────────

async def test_unsigned_build_has_no_release_signature(client, binary, single_tenant):
    ver = (await client.get("/api/agent/version/linux")).json()
    assert ver["release_signature"] is None and ver["release_key_id"] is None


async def test_release_signature_is_relayed_and_verifies(client, binary, multi_tenant):
    key, doc = _release_sign(binary)
    ver = (await client.get("/api/agent/version/linux")).json()

    assert ver["multi_tenant"] is True
    assert ver["release_key_id"] == doc["key_id"]
    assert ver["release_signature"] == doc["signature"]
    # What the agent verifies: the release message over the announced hash.
    key.public_key().verify(bytes.fromhex(ver["release_signature"]),
                            agent_release.release_message("linux", ver["hash"]))
    assert ver["hash"] == hashlib.sha256(binary.read_bytes()).hexdigest()


async def test_stale_release_signature_is_not_relayed(client, binary, single_tenant):
    _release_sign(binary)
    binary.write_bytes(binary.read_bytes() + b"rebuilt")
    ver = (await client.get("/api/agent/version/linux")).json()
    assert ver["release_signature"] is None


async def test_release_signature_for_another_platform_is_not_relayed(client, binary, single_tenant):
    _release_sign(binary, platform="windows")
    ver = (await client.get("/api/agent/version/linux")).json()
    assert ver["release_signature"] is None


# ── installers and heartbeat ─────────────────────────────────────────────────

async def _seed_install_token(sf):
    from models.agent_install_token import AgentInstallToken
    async with sf() as db:
        db.add(AgentInstallToken(token_hash=agents._hash_install_token(RAW_INSTALL), prefix="it",
                                 expires_at=datetime.utcnow() + timedelta(hours=1)))
        await db.commit()


@pytest.mark.parametrize("platform", ["linux", "windows"])
async def test_single_tenant_installer_writes_the_server_key(auth_client, single_tenant, platform):
    client, sf = auth_client
    await _seed_install_token(sf)
    resp = await client.get(f"/install/{platform}?token={RAW_INSTALL}")
    assert resp.status_code == 200, resp.text
    assert f'"update_public_key": "{agent_signing.public_key_hex()}"' in resp.text
    assert "require_release_signature" not in resp.text


@pytest.mark.parametrize("platform", ["linux", "windows"])
async def test_multi_tenant_installer_requires_release_signatures(auth_client, multi_tenant, platform):
    client, sf = auth_client
    await _seed_install_token(sf)
    resp = await client.get(f"/install/{platform}?token={RAW_INSTALL}")
    assert resp.status_code == 200, resp.text
    assert '"require_release_signature": true' in resp.text
    assert "update_public_key" not in resp.text


@pytest.mark.parametrize("is_probe", [False, True])
@pytest.mark.parametrize("mt", [False, True])
async def test_heartbeat_reports_the_trust_flags(auth_client, monkeypatch, is_probe, mt):
    from models.agent import Agent

    monkeypatch.setattr(agent_release, "multi_tenant_mode", lambda: mt)
    client, sf = auth_client
    async with sf() as db:
        db.add(Agent(name="web01", hostname="web01", token=agents._hash_agent_token(RAW_AGENT),
                     platform="linux", is_probe=is_probe))
        await db.commit()
    resp = await client.post(
        "/api/agent/report",
        json={"hostname": "web01", "platform": "linux", "arch": "x86_64", "cpu_pct": 1.0},
        headers={"Authorization": f"Bearer {RAW_AGENT}"},
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["probe"] is is_probe
    assert cfg["multi_tenant"] is mt
