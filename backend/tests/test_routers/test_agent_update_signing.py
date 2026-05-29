"""Router tests for the signed agent-update endpoints."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from services import agent_signing


@pytest.fixture(autouse=True)
def _fresh_key():
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()
    yield
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()


async def test_update_public_key_endpoint(client):
    resp = await client.get("/api/agent/update-public-key")
    assert resp.status_code == 200
    pub = resp.json()["public_key"]
    assert len(bytes.fromhex(pub)) == 32


async def test_version_endpoint_shape(client):
    resp = await client.get("/api/agent/version/linux")
    assert resp.status_code == 200
    body = resp.json()
    assert "hash" in body and "signature" in body


async def test_version_signature_verifies_against_binary(client, tmp_path, monkeypatch):
    """The signature served for a binary must verify with the public key."""
    import routers.agents as agents

    binary = tmp_path / "nodeglow-agent-linux"
    binary.write_bytes(b"\x7fELF" + b"a real-ish agent binary" * 50)
    monkeypatch.setattr(agents, "_agent_binary_path", lambda platform: binary)
    agents._agent_sig_cache.clear()
    agents._agent_file_cache.clear()

    ver = (await client.get("/api/agent/version/linux")).json()
    pub_hex = (await client.get("/api/agent/update-public-key")).json()["public_key"]

    assert ver["hash"] and ver["signature"]
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    # Verifies over the exact served bytes — the check the Rust agent performs.
    pub.verify(bytes.fromhex(ver["signature"]), binary.read_bytes())


async def test_invalid_platform_rejected(client):
    resp = await client.get("/api/agent/version/bogus")
    assert resp.status_code == 400
