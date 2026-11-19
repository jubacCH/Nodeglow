"""Agent binaries per platform: linux (x86_64), linux-arm64 and windows.

The platform name is what installers and agents ask for and what the release
signature binds, so an arm64 build is never offered or verified as x86_64.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import routers.agents as agents
from services import agent_release


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    """Serve agent binaries from an empty tmp dir (the routes use relative paths)."""
    (tmp_path / "static").mkdir()
    monkeypatch.chdir(tmp_path)
    agents._agent_file_cache.clear()
    agents._agent_sig_cache.clear()
    yield tmp_path / "static"
    agents._agent_file_cache.clear()
    agents._agent_sig_cache.clear()


def _write(static: Path, name: str, data: bytes) -> Path:
    path = static / name
    path.write_bytes(data)
    return path


def _release_sign(path: Path, platform: str, key: Ed25519PrivateKey) -> dict:
    """What CI does (tools/agent-release-key/sign.sh), with a throwaway key."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = {
        "format": agent_release.SIGNATURE_FORMAT, "alg": "ed25519", "platform": platform,
        "key_id": agent_release.key_id(key.public_key().public_bytes_raw()), "sha256": sha,
        "signature": key.sign(agent_release.release_message(platform, sha)).hex(),
    }
    agent_release.signature_path(path).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    return doc


@pytest.mark.parametrize("platform,name", [
    ("linux", "nodeglow-agent-linux"),
    ("linux-arm64", "nodeglow-agent-linux-arm64"),
    ("windows", "nodeglow-agent.exe"),
])
async def test_download_serves_the_file_of_each_platform(client, static_dir, platform, name):
    for other in ("nodeglow-agent-linux", "nodeglow-agent-linux-arm64", "nodeglow-agent.exe"):
        _write(static_dir, other, other.encode() + b" bytes")
    resp = await client.get(f"/agents/download/{platform}")
    assert resp.status_code == 200
    assert resp.content == name.encode() + b" bytes"


async def test_unknown_platform_is_not_served_the_linux_binary(client, static_dir):
    _write(static_dir, "nodeglow-agent-linux", b"x86_64")
    resp = await client.get("/agents/download/mips")
    assert resp.status_code == 404


async def test_arm64_without_a_binary_is_404_not_the_python_fallback(client, static_dir):
    _write(static_dir, "nodeglow-agent-linux.py", b"#!/usr/bin/env python3\n")
    _write(static_dir, "nodeglow-agent-linux", b"x86_64")
    assert (await client.get("/agents/download/linux-arm64")).status_code == 404
    ver = (await client.get("/api/agent/version/linux-arm64")).json()
    assert ver["hash"] == "" and ver["release_signature"] is None


async def test_linux_still_falls_back_to_the_python_agent(client, static_dir):
    _write(static_dir, "nodeglow-agent-linux.py", b"#!/usr/bin/env python3\n")
    resp = await client.get("/agents/download/linux")
    assert resp.status_code == 200
    assert resp.content.startswith(b"#!")


async def test_version_relays_the_arm64_release_signature(client, static_dir):
    key = Ed25519PrivateKey.generate()
    arm = _write(static_dir, "nodeglow-agent-linux-arm64", b"\x7fELF aarch64" * 40)
    x86 = _write(static_dir, "nodeglow-agent-linux", b"\x7fELF x86_64" * 40)
    arm_doc = _release_sign(arm, "linux-arm64", key)
    _release_sign(x86, "linux", key)

    ver = (await client.get("/api/agent/version/linux-arm64")).json()
    assert ver["hash"] == hashlib.sha256(arm.read_bytes()).hexdigest()
    assert ver["release_signature"] == arm_doc["signature"]
    key.public_key().verify(bytes.fromhex(ver["release_signature"]),
                            agent_release.release_message("linux-arm64", ver["hash"]))


async def test_an_x86_64_signature_is_not_relayed_for_arm64(client, static_dir):
    """A .sig made for "linux" next to the arm64 binary is not passed on."""
    arm = _write(static_dir, "nodeglow-agent-linux-arm64", b"\x7fELF aarch64" * 40)
    _release_sign(arm, "linux", Ed25519PrivateKey.generate())
    ver = (await client.get("/api/agent/version/linux-arm64")).json()
    assert ver["hash"] and ver["release_signature"] is None


# ── installer: the build for the target's CPU ────────────────────────────────

def _platform_block(script: str) -> str:
    start = script.index('case "$(uname -m)" in')
    end = script.index("esac", start) + len("esac")
    return script[start:end]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("machine,expected", [
    ("x86_64", "linux"), ("amd64", "linux"),
    ("aarch64", "linux-arm64"), ("arm64", "linux-arm64"),
    ("armv7l", None), ("riscv64", None),
])
def test_installer_picks_the_build_by_uname(tmp_path, machine, expected):
    script = agents.render_linux_installer("http://ng.example:8000", "nt_abc")
    fake = tmp_path / "uname"
    fake.write_text(f"#!/bin/sh\necho {machine}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    snippet = _platform_block(script) + '\necho "$AGENT_PLATFORM"\n'
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    run = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, env=env)
    if expected is None:
        assert run.returncode == 1
        assert "unsupported architecture" in run.stdout
    else:
        assert run.returncode == 0, run.stderr
        assert run.stdout.strip() == expected


def test_installer_downloads_the_build_it_picked():
    script = agents.render_linux_installer("http://ng.example:8000", "nt_abc")
    assert '"$SERVER/agents/download/$AGENT_PLATFORM"' in script
    assert "/agents/download/linux\"" not in script
    # The architecture is checked before the host enrols.
    assert script.index('case "$(uname -m)" in') < script.index("/api/agent/enroll")
