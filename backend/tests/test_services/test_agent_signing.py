"""Tests for ed25519 agent-update signing.

The round-trip test mirrors exactly what the Rust agent does
(agent/src/updater.rs: hex-decode the 32-byte public key + 64-byte signature,
verify pure ed25519 over the raw downloaded bytes), so a green test here means a
server-produced signature will verify on the agent.
"""
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from services import agent_signing


@pytest.fixture(autouse=True)
def _fresh_key():
    """Each test starts without a cached key and removes any generated file."""
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()
    yield
    agent_signing._reset_for_tests()
    if agent_signing._KEY_FILE.exists():
        agent_signing._KEY_FILE.unlink()


def test_key_is_generated_and_persisted():
    pub1 = agent_signing.public_key_hex()
    assert agent_signing._KEY_FILE.exists()
    # A fresh process (cleared cache) must reload the SAME key from disk.
    agent_signing._reset_for_tests()
    assert agent_signing.public_key_hex() == pub1


def test_public_key_and_signature_sizes():
    assert len(bytes.fromhex(agent_signing.public_key_hex())) == 32
    sig = agent_signing.sign_bytes(b"the agent binary bytes")
    assert len(bytes.fromhex(sig)) == 64


def test_signature_verifies_with_public_key():
    data = b"\x7fELF" + b"fake binary payload" * 100
    sig_hex = agent_signing.sign_bytes(data)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(agent_signing.public_key_hex()))
    # Must not raise — this is the exact check the Rust client performs.
    pub.verify(bytes.fromhex(sig_hex), data)


def test_tampered_payload_fails_verification():
    data = b"\x7fELF original payload"
    sig_hex = agent_signing.sign_bytes(data)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(agent_signing.public_key_hex()))
    with pytest.raises(InvalidSignature):
        pub.verify(bytes.fromhex(sig_hex), data + b"tampered")
