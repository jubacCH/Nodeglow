"""Release signatures of agent binaries (P2a): the .sig format, its loader and
the CI signing script tools/agent-release-key/sign.sh.

All keys here are throwaway keys generated in the test; no real key is used.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services import agent_release

REPO = Path(__file__).resolve().parents[3]
SIGN_SH = REPO / "tools" / "agent-release-key" / "sign.sh"
RELEASE_KEYS_RS = REPO / "agent" / "src" / "release_keys.rs"


def _doc(path, platform="linux", key=None, **override):
    key = key or Ed25519PrivateKey.generate()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = {
        "format": agent_release.SIGNATURE_FORMAT, "alg": "ed25519", "platform": platform,
        "key_id": agent_release.key_id(key.public_key().public_bytes_raw()), "sha256": sha,
        "signature": key.sign(agent_release.release_message(platform, sha)).hex(),
    }
    doc.update(override)
    agent_release.signature_path(path).write_text(json.dumps(doc), encoding="utf-8")
    return doc, sha


@pytest.fixture
def binary(tmp_path):
    p = tmp_path / "nodeglow-agent-linux"
    p.write_bytes(b"\x7fELF binary bytes")
    return p


def test_release_message_is_pinned():
    # Must stay byte-identical to agent/src/updater.rs and sign.sh.
    assert agent_release.release_message("linux", "ABCD") == b"nodeglow-agent-binary:v1:linux:abcd"


def test_key_id_matches_the_rust_test_vector():
    # agent/src/updater.rs pins the same id for the key with private bytes 0..32.
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    assert agent_release.key_id(key.public_key().public_bytes_raw()) == "56475aa75463474c"


def test_loads_a_matching_signature(binary):
    doc, sha = _doc(binary)
    got = agent_release.load_release_signature(binary, "linux", sha)
    assert got == {"key_id": doc["key_id"], "signature": doc["signature"]}


def test_no_file_means_unsigned(binary):
    assert agent_release.load_release_signature(binary, "linux", "00" * 32) is None


@pytest.mark.parametrize("override", [
    {"format": "something-else"},
    {"alg": "rsa"},
    {"key_id": "xyz"},
    {"signature": "00" * 10},
    {"sha256": "nothex"},
])
def test_malformed_signatures_are_ignored(binary, override):
    _, sha = _doc(binary, **override)
    assert agent_release.load_release_signature(binary, "linux", sha) is None


def test_garbage_file_is_ignored(binary):
    agent_release.signature_path(binary).write_text("not json", encoding="utf-8")
    assert agent_release.load_release_signature(binary, "linux", "00" * 32) is None
    agent_release.signature_path(binary).write_text("[1, 2]", encoding="utf-8")
    assert agent_release.load_release_signature(binary, "linux", "00" * 32) is None


def test_wrong_platform_or_stale_hash_is_ignored(binary):
    _, sha = _doc(binary, platform="windows")
    assert agent_release.load_release_signature(binary, "linux", sha) is None
    _, sha = _doc(binary)
    assert agent_release.load_release_signature(binary, "linux", "ab" * 32) is None


def test_multi_tenant_mode_follows_the_tenancy_feature(monkeypatch):
    from extensions import registry

    monkeypatch.setitem(registry.features, "multi_tenancy", False)
    assert agent_release.multi_tenant_mode() is False
    monkeypatch.setitem(registry.features, "multi_tenancy", True)
    assert agent_release.multi_tenant_mode() is True


def test_the_compiled_key_list_is_a_placeholder_or_valid_keys():
    """release_keys.rs: every entry is the placeholder or a 64-hex public key."""
    import re

    src = RELEASE_KEYS_RS.read_text(encoding="utf-8")
    body = src.split("RELEASE_PUBLIC_KEYS", 1)[1]
    entries = re.findall(r'^\s*"([^"]*)",\s*$', body, flags=re.M)
    assert entries, "no entries found in RELEASE_PUBLIC_KEYS"
    for entry in entries:
        assert entry == "REPLACE WITH RELEASE KEY" or re.fullmatch(r"[0-9a-f]{64}", entry), entry


# ── sign.sh (what CI runs) ───────────────────────────────────────────────────

def _openssl_ok():
    if not shutil.which("sh") or not shutil.which("openssl"):
        return False
    out = subprocess.run(["openssl", "version"], capture_output=True, text=True)
    return out.returncode == 0 and not out.stdout.startswith(("OpenSSL 1.", "LibreSSL"))


needs_openssl = pytest.mark.skipif(not _openssl_ok(), reason="needs sh + OpenSSL 3")


def _write_pem(tmp_path, key):
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    p = tmp_path / "throwaway-release-key.pem"
    p.write_bytes(pem)
    return p


def _sh(*args):
    return subprocess.run(["sh", str(SIGN_SH), *map(str, args)], capture_output=True, text=True)


@needs_openssl
@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_sign_sh_output_verifies_and_loads(tmp_path, binary, platform):
    key = Ed25519PrivateKey.generate()
    pem = _write_pem(tmp_path, key)

    out = _sh("pubkey", pem)
    assert out.returncode == 0, out.stderr
    pub = key.public_key().public_bytes_raw()
    assert out.stdout.split() == [pub.hex(), agent_release.key_id(pub)]

    out = _sh("sign", pem, platform, binary)
    assert out.returncode == 0, out.stderr
    doc = json.loads(agent_release.signature_path(binary).read_text(encoding="utf-8"))
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert doc["sha256"] == sha and doc["platform"] == platform
    # Cross-implementation: openssl's signature verifies with `cryptography`
    # over the message the agent rebuilds.
    key.public_key().verify(bytes.fromhex(doc["signature"]),
                            agent_release.release_message(platform, sha))
    assert agent_release.load_release_signature(binary, platform, sha) == {
        "key_id": agent_release.key_id(pub), "signature": doc["signature"]}

    assert _sh("verify", pem, binary).returncode == 0
    binary.write_bytes(binary.read_bytes() + b"x")
    assert _sh("verify", pem, binary).returncode != 0


@needs_openssl
def test_sign_sh_refuses_a_non_ed25519_key(tmp_path, binary):
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    p = tmp_path / "ec.pem"
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    assert _sh("sign", p, "linux", binary).returncode != 0
    assert not agent_release.signature_path(binary).exists()
    assert _sh("sign", _write_pem(tmp_path, Ed25519PrivateKey.generate()), "macos", binary).returncode != 0
