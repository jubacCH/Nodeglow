"""Integration credentials cannot be redirected, and targets are SSRF-checked.

Editing an integration kept every stored secret whose form field was left
empty — also when the host changed. Anyone allowed to edit could point a
Proxmox integration at their own server and receive the API token on the next
poll. Now an endpoint change requires all stored secrets to be re-entered, and
only admins may create, change or delete integrations at all.
"""
import ipaddress
import socket
from unittest.mock import patch

import pytest

from tests.test_routers.conftest import make_client


class AdminUser:
    id = 1
    username = "admin"
    role = "admin"


class EditorUser:
    id = 2
    username = "ed"
    role = "editor"


async def _seed_proxmox(session_factory):
    from services import integration as int_svc
    async with session_factory() as db:
        cfg = await int_svc.create_config(db, "proxmox", "pve", {
            "host": "https://10.0.0.5:8006",
            "token_id": "root@pam!ng",
            "token_secret": "s3cret-token",
            "verify_ssl": True,
            "ssh_private_key": "",
        })
        return cfg.id


async def _stored(session_factory, cfg_id):
    from services import integration as int_svc
    async with session_factory() as db:
        cfg = await int_svc.get_config(db, cfg_id)
        return int_svc.decrypt_config(cfg.config_json)


@pytest.fixture
async def admin():
    async with make_client(fake_user=AdminUser()) as pair:
        yield pair


async def test_host_change_without_secret_is_rejected(admin):
    client, sf = admin
    cfg_id = await _seed_proxmox(sf)
    resp = await client.patch(f"/api/integration/proxmox/{cfg_id}",
                              json={"host": "https://10.66.66.66:8006"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "secrets_required"
    assert body["missing_fields"] == ["token_secret"]  # empty ssh key is not "stored"
    stored = await _stored(sf, cfg_id)
    assert stored["host"] == "https://10.0.0.5:8006"


async def test_host_change_with_secret_is_accepted(admin):
    client, sf = admin
    cfg_id = await _seed_proxmox(sf)
    resp = await client.patch(f"/api/integration/proxmox/{cfg_id}",
                              json={"host": "https://10.0.0.6:8006", "token_secret": "new"})
    assert resp.status_code == 200, resp.text
    stored = await _stored(sf, cfg_id)
    assert stored["host"] == "https://10.0.0.6:8006"
    assert stored["token_secret"] == "new"


async def test_edit_without_host_change_keeps_secret(admin):
    client, sf = admin
    cfg_id = await _seed_proxmox(sf)
    # The frontend sends name + whatever the user typed; secrets stay empty.
    resp = await client.patch(f"/api/integration/proxmox/{cfg_id}",
                              json={"name": "renamed", "token_secret": "",
                                    "host": "https://10.0.0.5:8006/"})
    assert resp.status_code == 200, resp.text
    stored = await _stored(sf, cfg_id)
    assert stored["token_secret"] == "s3cret-token"


async def test_integration_changes_are_audited(admin):
    from models.audit import AuditLog
    from sqlalchemy import select

    client, sf = admin
    cfg_id = await _seed_proxmox(sf)
    await client.patch(f"/api/integration/proxmox/{cfg_id}",
                       json={"host": "https://10.0.0.7:8006", "token_secret": "x"})
    await client.delete(f"/api/integration/proxmox/{cfg_id}")
    async with sf() as db:
        rows = (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    actions = [r.action for r in rows]
    assert actions == ["integration.update", "integration.delete"]
    assert rows[0].username == "admin"
    assert "s3cret" not in (rows[0].details or "") and '"x"' not in (rows[0].details or "")
    assert "10.0.0.7" in rows[0].details


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/integration/proxmox/create", {"host": "https://10.0.0.9"}),
    ("PATCH", "/api/integration/proxmox/{id}", {"name": "x"}),
    ("DELETE", "/api/integration/proxmox/{id}", None),
])
async def test_editor_cannot_manage_integrations(method, path, body):
    async with make_client(fake_user=EditorUser()) as (client, sf):
        cfg_id = await _seed_proxmox(sf)
        kwargs = {"json": body} if body is not None else {}
        resp = await client.request(method, path.format(id=cfg_id), **kwargs)
        assert resp.status_code == 403


# ── _validate_host ──────────────────────────────────────────────────────────

from routers.integrations import _validate_host  # noqa: E402


@pytest.mark.parametrize("value", [
    "localhost", "http://localhost:8080", "127.0.0.1", "127.8.9.10", "[::1]:443",
    "http://[::ffff:127.0.0.1]/", "0.0.0.0", "0.1.2.3", "::",
    "169.254.169.254", "http://169.254.169.254/latest/meta-data", "169.254.1.1",
    "fe80::1", "metadata.google.internal",
    "db", "clickhouse:8123", "http://updater:9100", "nodeglow", "frontend:3000",
    "http://DB:5432/", "localhost.",
])
def test_validate_host_blocks(value):
    assert _validate_host(value, resolve=False) is not None, value


@pytest.mark.parametrize("value", [
    "10.0.0.1", "https://10.10.30.5:8006", "172.20.1.1", "192.168.1.1:443",
    "fd12:3456::1", "pve.lan", "https://truenas.example.com", "8.8.8.8", "",
])
def test_validate_host_allows_lan_and_public(value):
    assert _validate_host(value, resolve=False) is None, value


def _fake_getaddrinfo(mapping):
    def fake(host, port, *a, **kw):
        if host not in mapping:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]
    return fake


def test_validate_host_resolves_names():
    mapping = {"evil.example": ["127.0.0.1"], "meta.example": ["169.254.169.254"],
               "nas.lan": ["192.168.1.20"], "mixed.example": ["10.0.0.1", "127.0.0.1"]}
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(mapping)):
        assert "Loopback" in _validate_host("https://evil.example:8443")
        assert "metadata" in _validate_host("meta.example")
        assert _validate_host("nas.lan") is None
        assert _validate_host("mixed.example") is not None
        # Unresolvable: allowed (nothing reachable), not an error.
        assert _validate_host("offline.lan") is None


async def test_create_rejects_internal_target(admin):
    client, _sf = admin
    resp = await client.post("/api/integration/proxmox/create",
                             json={"host": "https://db:5432", "token_id": "a", "token_secret": "b"})
    assert resp.status_code == 400
    assert "Internal service" in resp.json()["error"]
