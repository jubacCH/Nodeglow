"""Tenancy migrations (039–043) against a real PostgreSQL.

The migration chain cannot run on SQLite (revision 022 uses a PostgreSQL
regex), so these tests need a server: set ``NODEGLOW_PG_TEST_URL`` to an
admin DSN, e.g. ``postgresql://postgres@127.0.0.1:5432/postgres``. Each test
creates and drops its own database. Without the variable they are skipped;
the CI migrations job covers ``upgrade head`` + ``alembic check`` regardless.

Covered:
* upgrade from 038 with seeded data: every row lands in tenant 1 / site 1,
  users become members with their role, ids and references survive;
* the schema-level guarantees: a cross-tenant reference is rejected, and
  deleting a probe clears only ``probe_id`` (``SET NULL (probe_id)``);
* downgrade back to 038 restores the old shape with the data intact;
* a fresh install built from the models (``migrate.py``) has the default
  tenant rows and agrees with the migration chain (``alembic check``);
* 042 (settings split): tenant keys move to tenant 1 with their encrypted
  flag, installation keys stay, the dashboard layout becomes every user's;
  the downgrade puts them back;
* 043 (auth binding): memberships for users created after 039, admins
  become operators, audit rows get their tenant's provider, sessions gain
  ``active_tenant_id``; the downgrade refreshes ``users.role``.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADMIN_URL = os.environ.get("NODEGLOW_PG_TEST_URL", "")
BACKEND = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="NODEGLOW_PG_TEST_URL not set")


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _admin(sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_asyncpg_dsn(ADMIN_URL))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture
def pg_db():
    name = f"ng_tenancy_{uuid.uuid4().hex[:10]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    base = _asyncpg_dsn(ADMIN_URL).rsplit("/", 1)[0]
    yield f"{base}/{name}"
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _run(args: list[str], dsn: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    env.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    env.setdefault("DATA_DIR", str(BACKEND / "tests" / ".test_data"))
    env.pop("NODEGLOW_DISABLE_EE", None)
    proc = subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"{args} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    return _run(["-m", "alembic", *args], dsn)


async def _q(dsn: str, sql: str, *params):
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        if sql.lstrip().upper().startswith("SELECT"):
            return await conn.fetch(sql, *params)
        return await conn.execute(sql, *params)
    finally:
        await conn.close()


def q(dsn: str, sql: str, *params):
    return asyncio.run(_q(dsn, sql, *params))


SEED_038 = """
INSERT INTO settings (key, value, encrypted) VALUES ('site_name', 'ACME Monitoring', false);
INSERT INTO settings (key, value, encrypted) VALUES ('notify_enabled', '1', false),
  ('smtp_password', 'gAAAAA-ciphertext', true), ('syslog_port', '5514', false),
  ('dashboard_layout', '[{"id": "hosts", "x": 0, "y": 0, "w": 12, "h": 4}]', false),
  ('_lockout:admin', '[]', false);
INSERT INTO users (id, username, password_hash, role, auth_source, created_at)
  VALUES (1, 'admin', 'x', 'admin', 'local', now()), (2, 'viewer', 'x', 'readonly', 'local', now()),
         (3, 'legacy', 'x', NULL, 'local', now());
INSERT INTO agents (id, name, hostname, token, enabled, is_probe, created_at)
  VALUES (1, 'probe-1', 'Probe01', 'tok-1', true, true, now());
INSERT INTO ping_hosts (id, name, hostname, enabled, check_type, probe_id, created_at)
  VALUES (1, 'router', '10.0.0.1', true, 'icmp', 1, now());
INSERT INTO ping_hosts (id, name, hostname, enabled, check_type, parent_id, created_at)
  VALUES (2, 'switch', '10.0.0.2', true, 'icmp', 1, now());
INSERT INTO incidents (id, rule, title, severity, status, created_at, updated_at)
  VALUES (1, 'host_down', 'router down', 'critical', 'open', now(), now());
INSERT INTO incident_events (incident_id, timestamp, event_type, summary)
  VALUES (1, now(), 'created', 'created');
INSERT INTO log_templates (id, template_hash, template, count) VALUES (1, 'abc', 'x <*>', 3);
INSERT INTO precursor_patterns (template_id, precedes_event) VALUES (1, 'host_down');
INSERT INTO snapshots (entity_type, entity_id, timestamp, ok) VALUES ('proxmox', 1, now(), true);
INSERT INTO discovered_ports (host_id, port) VALUES (1, 22);
INSERT INTO credentials (id, name, type, data_json) VALUES (1, 'snmp', 'snmp_v2c', 'x');
INSERT INTO snmp_host_configs (host_id, credential_id) VALUES (1, 1);
INSERT INTO user_preferences (user_id, dashboard_seen_at) VALUES (1, now());
INSERT INTO audit_logs (timestamp, action) VALUES (now(), 'login');
"""


def _seed(dsn: str) -> None:
    for stmt in filter(None, (s.strip() for s in SEED_038.split(";"))):
        q(dsn, stmt)
    for table in ("users", "agents", "ping_hosts", "incidents", "log_templates", "credentials"):
        q(dsn, f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}))")


def test_upgrade_from_038_with_data(pg_db):
    _alembic(pg_db, "upgrade", "038")
    _seed(pg_db)
    _alembic(pg_db, "upgrade", "head")

    tenant = q(pg_db, "SELECT id, provider_id, name, slug, status FROM tenants")
    assert [tuple(r) for r in tenant] == [(1, 1, "ACME Monitoring", "default", "active")]
    assert [tuple(r) for r in q(pg_db, "SELECT id, tenant_id FROM sites")] == [(1, 1)]
    members = q(pg_db, "SELECT user_id, tenant_id, role FROM tenant_memberships ORDER BY user_id")
    assert [tuple(r) for r in members] == [(1, 1, "admin"), (2, 1, "readonly"), (3, 1, "admin")]

    for table in ("ping_hosts", "agents", "incidents", "incident_events", "log_templates",
                  "precursor_patterns", "snapshots", "discovered_ports", "credentials",
                  "snmp_host_configs", "user_preferences", "audit_logs"):
        assert q(pg_db, f"SELECT DISTINCT tenant_id FROM {table}")[0][0] == 1, table
    hosts = q(pg_db, "SELECT id, parent_id, probe_id, site_id FROM ping_hosts ORDER BY id")
    assert [tuple(r) for r in hosts] == [(1, None, 1, 1), (2, 1, None, 1)]

    # tenant_id has no server default: a row without one is refused.
    with pytest.raises(Exception, match="null value"):
        q(pg_db, "INSERT INTO syslog_views (name, filters_json) VALUES ('v', '{}')")

    # The serial sequences moved past the seeded ids.
    q(pg_db, "INSERT INTO providers (name, slug, created_at) VALUES ('MSP', 'msp', now())")
    q(pg_db, "INSERT INTO tenants (provider_id, name, slug, status, created_at) "
             "VALUES (2, 'Other', 'other', 'active', now())")
    assert q(pg_db, "SELECT id FROM tenants WHERE slug = 'other'")[0][0] == 2

    # Cross-tenant references are impossible at the schema level.
    with pytest.raises(Exception, match="fk_ping_hosts_probe_id"):
        q(pg_db, "INSERT INTO ping_hosts (name, hostname, tenant_id, probe_id) "
                 "VALUES ('b', 'b', 2, 1)")
    with pytest.raises(Exception, match="fk_discovered_ports_host"):
        q(pg_db, "INSERT INTO discovered_ports (tenant_id, host_id, port) VALUES (2, 1, 80)")
    # Hostnames are unique per tenant, not per installation.
    q(pg_db, "INSERT INTO agents (name, hostname, token, tenant_id, is_probe) "
             "VALUES ('p', 'probe01', 'tok-2', 2, false)")
    with pytest.raises(Exception, match="ix_agents_hostname_lower"):
        q(pg_db, "INSERT INTO agents (name, hostname, token, tenant_id, is_probe) "
                 "VALUES ('p', 'PROBE01', 'tok-3', 1, false)")

    # Deleting a probe clears only probe_id; the host stays in its tenant.
    q(pg_db, "DELETE FROM agents WHERE id = 1")
    assert tuple(q(pg_db, "SELECT tenant_id, probe_id FROM ping_hosts WHERE id = 1")[0]) == (1, None)
    # Deleting a host cascades to its tenant-scoped children.
    q(pg_db, "DELETE FROM ping_hosts WHERE id = 2")
    q(pg_db, "DELETE FROM ping_hosts WHERE id = 1")
    assert q(pg_db, "SELECT count(*) FROM discovered_ports")[0][0] == 0
    assert q(pg_db, "SELECT count(*) FROM snmp_host_configs")[0][0] == 0

    _alembic(pg_db, "check")


def test_settings_split_and_auth_binding(pg_db):
    _alembic(pg_db, "upgrade", "038")
    _seed(pg_db)
    _alembic(pg_db, "upgrade", "041")
    # A user created between 039 and 043 (setup wizard, users API, LDAP) got
    # no membership; neither did a session row know its tenant.
    q(pg_db, "INSERT INTO users (id, username, password_hash, role, auth_source, created_at) "
             "VALUES (4, 'late', 'x', 'editor', 'ldap', now())")
    q(pg_db, "INSERT INTO sessions (token, user_id, expires_at) VALUES ('t1', 1, now() + interval '1 day')")
    # ...and a role changed through the users API wrote only users.role.
    q(pg_db, "UPDATE users SET role = 'editor' WHERE id = 3")
    assert q(pg_db, "SELECT role FROM tenant_memberships WHERE user_id = 3")[0][0] == "admin"
    _alembic(pg_db, "upgrade", "head")

    # 042: tenant keys moved (encrypted flag kept), installation keys stayed.
    ts = {r[0]: (r[1], r[2]) for r in q(pg_db, "SELECT key, value, encrypted FROM tenant_settings "
                                             "WHERE tenant_id = 1")}
    assert ts["site_name"] == ("ACME Monitoring", False)
    assert ts["notify_enabled"] == ("1", False)
    assert ts["smtp_password"] == ("gAAAAA-ciphertext", True)
    remaining = {r[0] for r in q(pg_db, "SELECT key FROM settings")}
    assert {"syslog_port", "_lockout:admin"} <= remaining
    assert not remaining & {"site_name", "notify_enabled", "smtp_password", "dashboard_layout"}
    # The layout is now every user's (user 1 already had a preferences row).
    layouts = q(pg_db, "SELECT user_id, dashboard_layout FROM user_preferences ORDER BY user_id")
    assert [r[0] for r in layouts] == [1, 2, 3, 4]
    assert all('"hosts"' in (r[1] or "") for r in layouts)

    # 043: memberships for everybody, admins are operators, audit provider.
    members = q(pg_db, "SELECT user_id, role FROM tenant_memberships ORDER BY user_id")
    # users.role won over the stale 039 copy of user 3.
    assert [tuple(r) for r in members] == [(1, "admin"), (2, "readonly"), (3, "editor"), (4, "editor")]
    ops = q(pg_db, "SELECT id FROM users WHERE is_operator ORDER BY id")
    assert [r[0] for r in ops] == [1]
    assert q(pg_db, "SELECT active_tenant_id FROM sessions")[0][0] is None
    assert [tuple(r) for r in q(pg_db, "SELECT tenant_id, provider_id, acting_via FROM audit_logs")] \
        == [(1, 1, None)]
    # A session pointing at a tenant that goes away loses only the pointer.
    q(pg_db, "INSERT INTO tenants (provider_id, name, slug, status, created_at) "
             "VALUES (1, 'B', 'b', 'active', now())")
    q(pg_db, "UPDATE sessions SET active_tenant_id = 2")
    q(pg_db, "DELETE FROM tenants WHERE id = 2")
    assert q(pg_db, "SELECT active_tenant_id FROM sessions")[0][0] is None
    _alembic(pg_db, "check")

    # Downgrade: the settings come back, users.role reflects the memberships.
    q(pg_db, "UPDATE tenant_memberships SET role = 'readonly' WHERE user_id = 3")
    q(pg_db, "UPDATE users SET role = 'admin' WHERE id = 3")
    _alembic(pg_db, "downgrade", "041")
    back = {r[0]: (r[1], r[2]) for r in q(pg_db, "SELECT key, value, encrypted FROM settings")}
    assert back["site_name"] == ("ACME Monitoring", False)
    assert back["smtp_password"] == ("gAAAAA-ciphertext", True)
    assert '"hosts"' in back["dashboard_layout"][0]
    assert q(pg_db, "SELECT count(*) FROM tenant_settings")[0][0] == 0
    assert q(pg_db, "SELECT role FROM users WHERE id = 3")[0][0] == "readonly"
    cols = {r[0] for r in q(pg_db, "SELECT column_name FROM information_schema.columns "
                                   "WHERE table_name IN ('users', 'sessions', 'audit_logs', 'user_preferences')")}
    assert not cols & {"is_operator", "active_tenant_id", "provider_id", "acting_via", "dashboard_layout"}
    _alembic(pg_db, "upgrade", "head")
    _alembic(pg_db, "check")


RESTORE_SCRIPT = r"""
import asyncio, json, sys
from models.base import AsyncSessionLocal, engine
from services import backup
from services.tenancy import tenant_scope

async def main():
    data = json.loads(sys.argv[1])
    with tenant_scope(1):
        async with AsyncSessionLocal() as db:
            print(json.dumps(await backup.import_backup(db, data)))
    await engine.dispose()

asyncio.run(main())
"""


def test_restoring_a_pre_042_backup_takes_its_settings_and_roles(pg_db):
    """A backup from before the settings split / auth binding: its tenant
    settings land in tenant 1 and replace the current ones; users.role wins
    over memberships (none in the backup) and decides is_operator."""
    import json

    _run(["migrate.py"], pg_db)  # a current installation
    q(pg_db, "INSERT INTO tenant_settings (tenant_id, key, value, encrypted) VALUES "
             "(1, 'site_name', 'Current', false), (1, 'notify_enabled', '1', false)")
    old = {
        "_meta": {"format": "nodeglow-backup", "version": "1.0"},
        "tables": {
            "settings": [{"key": "site_name", "value": "Restored", "encrypted": False},
                         {"key": "syslog_port", "value": "5514", "encrypted": False}],
            "users": [{"id": 1, "username": "root", "password_hash": "x", "role": "admin",
                       "auth_source": "local"},
                      {"id": 2, "username": "eve", "password_hash": "x", "role": "readonly",
                       "auth_source": "local"}],
        },
    }
    _run(["-c", RESTORE_SCRIPT, json.dumps(old)], pg_db)
    ts = {r[0]: r[1] for r in q(pg_db, "SELECT key, value FROM tenant_settings WHERE tenant_id = 1")}
    assert ts == {"site_name": "Restored"}
    assert {r[0] for r in q(pg_db, "SELECT key FROM settings")} >= {"syslog_port"}
    assert q(pg_db, "SELECT count(*) FROM settings WHERE key = 'site_name'")[0][0] == 0
    members = q(pg_db, "SELECT user_id, role FROM tenant_memberships ORDER BY user_id")
    assert [tuple(r) for r in members] == [(1, "admin"), (2, "readonly")]
    assert [r[0] for r in q(pg_db, "SELECT id FROM users WHERE is_operator ORDER BY id")] == [1]


def test_downgrade_to_038_keeps_data(pg_db):
    _alembic(pg_db, "upgrade", "038")
    _seed(pg_db)
    _alembic(pg_db, "upgrade", "head")
    _alembic(pg_db, "downgrade", "038")

    cols = {r[0] for r in q(pg_db, "SELECT column_name FROM information_schema.columns "
                                   "WHERE table_name = 'ping_hosts'")}
    assert "tenant_id" not in cols and "site_id" not in cols
    assert q(pg_db, "SELECT to_regclass('public.tenants')")[0][0] is None
    assert q(pg_db, "SELECT count(*) FROM ping_hosts")[0][0] == 2
    assert q(pg_db, "SELECT count(*) FROM user_preferences")[0][0] == 1
    # ...and back up again.
    _alembic(pg_db, "upgrade", "head")
    _alembic(pg_db, "check")


def test_fresh_install_from_models_matches_chain(pg_db):
    _run(["migrate.py"], pg_db)
    assert [tuple(r) for r in q(pg_db, "SELECT id, provider_id FROM tenants")] == [(1, 1)]
    assert [tuple(r) for r in q(pg_db, "SELECT id, tenant_id FROM sites")] == [(1, 1)]
    q(pg_db, "INSERT INTO providers (name, slug, created_at) VALUES ('MSP', 'msp', now())")
    assert q(pg_db, "SELECT max(id) FROM providers")[0][0] == 2
    _alembic(pg_db, "check")
    # Running it again (the existing-install path) is a no-op.
    _run(["migrate.py"], pg_db)
