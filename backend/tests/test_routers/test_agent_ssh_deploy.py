"""Agent deploy over SSH: the installer, tokens, host keys, jobs and routes.

See docs/specs/2026-11-18-agent-ssh-deploy-design.md.
"""
from datetime import datetime, timedelta

import pytest

import services.shared_state as ss


@pytest.fixture(autouse=True)
def _reset_limits():
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
