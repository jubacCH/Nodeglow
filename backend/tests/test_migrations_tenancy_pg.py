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
* 044 / migrate.py (row-level security, phase 7): a fresh install and an
  upgrade from 043 both end with RLS enabled and forced on every tenant-owned
  table when ``NODEGLOW_RLS=on``; as ``nodeglow_app`` a raw select without
  the tenant setting returns nothing, with it only that tenant, system scope
  everything; composite FKs still reject cross-tenant references; the
  downgrade removes the policies; with the flag off nothing changes; a user
  without CREATEROLE upgrades with a warning; and a subset of the router
  tests runs against PostgreSQL as the app role with RLS on.
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


def _run(args: list[str], dsn: str, **env_overrides: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("NODEGLOW_DISABLE_EE", "NODEGLOW_RLS", "DATABASE_OWNER_URL",
                "MIGRATION_DATABASE_URL"):
        env.pop(key, None)
    env["DATABASE_URL"] = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    env.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    env.setdefault("DATA_DIR", str(BACKEND / "tests" / ".test_data"))
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"{args} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _alembic(dsn: str, *args: str, **env_overrides: str) -> subprocess.CompletedProcess:
    return _run(["-m", "alembic", *args], dsn, **env_overrides)


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


def test_restoring_a_current_backup_keeps_its_memberships(pg_db):
    """TRUNCATE users CASCADE wipes memberships and preferences; a current
    backup brings them back as they were (the membership role wins over
    users.role), and a user without one gets one."""
    import json

    _run(["migrate.py"], pg_db)
    backup = {
        "_meta": {"format": "nodeglow-backup", "version": "1.0"},
        "tables": {
            "settings": [{"key": "syslog_port", "value": "1514", "encrypted": False}],
            "users": [{"id": 1, "username": "root", "password_hash": "x", "role": "admin",
                       "auth_source": "local", "is_operator": True},
                      {"id": 2, "username": "ed", "password_hash": "x", "role": "admin",
                       "auth_source": "local", "is_operator": False},
                      {"id": 3, "username": "new", "password_hash": "x", "role": "readonly",
                       "auth_source": "local", "is_operator": False}],
            "tenant_memberships": [
                {"user_id": 1, "tenant_id": 1, "role": "admin", "created_at": None},
                {"user_id": 2, "tenant_id": 1, "role": "editor", "created_at": None}],
            "tenant_settings": [{"tenant_id": 1, "key": "site_name", "value": "Backup", "encrypted": False}],
            "user_preferences": [{"user_id": 2, "tenant_id": 1, "dashboard_layout": "[]"}],
        },
    }
    # As an export writes them: timestamps are ISO strings.
    for m in backup["tables"]["tenant_memberships"]:
        m["created_at"] = "2026-10-01T12:00:00"
    for u in backup["tables"]["users"]:
        u["created_at"] = "2026-01-01T00:00:00.123456"
    _run(["-c", RESTORE_SCRIPT, json.dumps(backup)], pg_db)
    members = q(pg_db, "SELECT user_id, role FROM tenant_memberships ORDER BY user_id")
    assert [tuple(r) for r in members] == [(1, "admin"), (2, "editor"), (3, "readonly")]
    assert q(pg_db, "SELECT count(*) FROM user_preferences")[0][0] == 1
    assert q(pg_db, "SELECT value FROM tenant_settings WHERE key = 'site_name'")[0][0] == "Backup"


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


# ── Row-level security (044, migrate.py) ─────────────────────────────────────

APP_ROLE = "nodeglow_app"
APP_PASSWORD = "app-role-test-password"


def _app_dsn(dsn: str) -> str:
    from sqlalchemy.engine import make_url

    url = make_url(dsn).set(username=APP_ROLE, password=APP_PASSWORD)
    return url.render_as_string(hide_password=False)


def _rls_env(dsn: str, flag: str = "on") -> dict:
    """The split configuration of a new install: migrations as the owner,
    the app as nodeglow_app, RLS on."""
    asyncpg_url = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return {
        "DATABASE_OWNER_URL": asyncpg_url,
        "DATABASE_URL": _app_dsn(asyncpg_url),
        "NODEGLOW_RLS": flag,
    }


def _live_tenant_tables() -> list[str]:
    from services.tenancy import tenant_tables

    return sorted(t.name for t in tenant_tables())


def _rls_flags(dsn: str) -> dict[str, tuple[bool, bool]]:
    rows = q(dsn, "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                  "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
                  "WHERE c.relname = ANY($1::text[])", _live_tenant_tables())
    return {r[0]: (r[1], r[2]) for r in rows}


def _policies(dsn: str) -> set[str]:
    return {r[0] for r in q(dsn, "SELECT tablename FROM pg_policies "
                                 "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'")}


def _assert_rls_everywhere(dsn: str) -> None:
    live = _live_tenant_tables()
    flags = _rls_flags(dsn)
    assert sorted(flags) == live
    assert [t for t, f in flags.items() if f != (True, True)] == []
    assert _policies(dsn) == set(live)
    role = q(dsn, "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = $1", APP_ROLE)
    assert tuple(role[0]) == (False, False, True)
    owned = q(dsn, "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                   "WHERE r.rolname = $1", APP_ROLE)
    assert owned[0][0] == 0


async def _aq_app(dsn: str, sql: str, *params, tenant=None, system=False):
    import asyncpg

    conn = await asyncpg.connect(_app_dsn(dsn))
    try:
        async with conn.transaction():
            if tenant is not None:
                await conn.execute("SELECT set_config('nodeglow.tenant_id', $1, true)", str(tenant))
            if system:
                await conn.execute("SELECT set_config('nodeglow.system', 'on', true)")
            if sql.lstrip().upper().startswith("SELECT"):
                return await conn.fetch(sql, *params)
            return await conn.execute(sql, *params)
    finally:
        await conn.close()


def q_app(dsn: str, sql: str, *params, tenant=None, system=False):
    return asyncio.run(_aq_app(dsn, sql, *params, tenant=tenant, system=system))


SEED_TWO_TENANTS = """
INSERT INTO tenants (id, provider_id, name, slug, status, created_at) VALUES (2, 1, 'B', 'b', 'active', now());
INSERT INTO sites (id, tenant_id, name, created_at) VALUES (2, 2, 'B', now());
INSERT INTO agents (id, tenant_id, name, hostname, token, enabled, is_probe, created_at)
  VALUES (21, 1, 'probe-a', 'probe-a', 'tok-a', true, true, now());
INSERT INTO ping_hosts (id, tenant_id, name, hostname, enabled, check_type, created_at)
  VALUES (21, 1, 'host-a', '10.0.0.1', true, 'icmp', now()),
         (22, 2, 'CANARY-B', '10.0.0.1', true, 'icmp', now());
"""


def _seed_two_tenants(dsn: str) -> None:
    for stmt in filter(None, (s.strip() for s in SEED_TWO_TENANTS.split(";"))):
        q(dsn, stmt)  # as the superuser, who bypasses RLS


def _assert_isolation(dsn: str) -> None:
    """Raw SQL as nodeglow_app: the policies decide, whatever the SQL says."""
    _seed_two_tenants(dsn)

    # No setting: nothing to read, nothing to change.
    assert q_app(dsn, "SELECT count(*) FROM ping_hosts")[0][0] == 0
    assert q_app(dsn, "UPDATE ping_hosts SET name = 'x'") == "UPDATE 0"
    assert q_app(dsn, "DELETE FROM ping_hosts") == "DELETE 0"
    # Global tables are not affected.
    assert q_app(dsn, "SELECT count(*) FROM tenants")[0][0] == 2
    # One tenant: only its rows, even when the SQL asks for the other's.
    names = {r[0] for r in q_app(dsn, "SELECT name FROM ping_hosts", tenant=1)}
    assert "host-a" in names and "CANARY-B" not in names
    assert q_app(dsn, "SELECT count(*) FROM ping_hosts WHERE tenant_id = 2", tenant=1)[0][0] == 0
    assert q_app(dsn, "UPDATE ping_hosts SET name = 'x' WHERE id = 22", tenant=1) == "UPDATE 0"
    # System scope: everything.
    total = q(dsn, "SELECT count(*) FROM ping_hosts")[0][0]
    assert q_app(dsn, "SELECT count(*) FROM ping_hosts", system=True)[0][0] == total
    # WITH CHECK: a row for another tenant cannot be written, nor moved there.
    with pytest.raises(Exception, match="row-level security"):
        q_app(dsn, "INSERT INTO ping_hosts (tenant_id, name, hostname, enabled, check_type, created_at) "
                   "VALUES (2, 'sneaky', 'x', true, 'icmp', now())", tenant=1)
    with pytest.raises(Exception, match="row-level security"):
        q_app(dsn, "UPDATE ping_hosts SET tenant_id = 2 WHERE id = 21", tenant=1)
    with pytest.raises(Exception, match="row-level security"):
        q_app(dsn, "INSERT INTO ping_hosts (tenant_id, name, hostname, enabled, check_type, created_at) "
                   "VALUES (1, 'nobody', 'x', true, 'icmp', now())")
    # Composite FKs still reject a cross-tenant reference, even in system scope.
    with pytest.raises(Exception, match="fk_ping_hosts_probe_id"):
        q_app(dsn, "INSERT INTO ping_hosts (tenant_id, name, hostname, enabled, check_type, probe_id, "
                   "created_at) VALUES (2, 'b', 'b', true, 'icmp', 21, now())", system=True)
    # The app role owns nothing: no TRUNCATE (it bypasses RLS), no switching RLS off.
    with pytest.raises(Exception, match="permission denied"):
        q_app(dsn, "TRUNCATE ping_hosts", system=True)
    with pytest.raises(Exception, match="must be owner"):
        q_app(dsn, "ALTER TABLE ping_hosts DISABLE ROW LEVEL SECURITY", system=True)
    # Nor write the schema revision.
    with pytest.raises(Exception, match="permission denied"):
        q_app(dsn, "UPDATE alembic_version SET version_num = '001'", system=True)


def test_fresh_install_with_rls(pg_db):
    env = _rls_env(pg_db)
    proc = _run(["migrate.py"], pg_db, **env)
    assert "row-level security enabled and forced" in proc.stdout + proc.stderr
    _assert_rls_everywhere(pg_db)
    _assert_isolation(pg_db)
    _alembic(pg_db, "check", **env)

    # Again (every start): nothing to change, still everywhere.
    _run(["migrate.py"], pg_db, **env)
    _assert_rls_everywhere(pg_db)

    # Switching the flag off at the next start turns RLS off again (the
    # policies stay); the app then reads without settings, as before.
    _run(["migrate.py"], pg_db, **_rls_env(pg_db, flag="off"))
    assert set(_rls_flags(pg_db).values()) == {(False, False)}
    assert _policies(pg_db) == set(_live_tenant_tables())
    assert q_app(pg_db, "SELECT count(*) FROM ping_hosts")[0][0] == 2


def test_upgrade_from_043_with_rls_and_downgrade(pg_db):
    _alembic(pg_db, "upgrade", "038")
    _seed(pg_db)
    _alembic(pg_db, "upgrade", "043")
    env = _rls_env(pg_db)
    _alembic(pg_db, "upgrade", "head", **env)
    _assert_rls_everywhere(pg_db)
    # The seeded rows are tenant 1's.
    assert q_app(pg_db, "SELECT count(*) FROM ping_hosts", tenant=1)[0][0] == 2
    assert q_app(pg_db, "SELECT count(*) FROM incident_events", tenant=1)[0][0] == 1
    assert q_app(pg_db, "SELECT count(*) FROM incident_events")[0][0] == 0
    _alembic(pg_db, "check", **env)

    # Downgrade: policies gone, RLS off; roles and grants stay, so an app
    # already running as nodeglow_app keeps working (without RLS).
    _alembic(pg_db, "downgrade", "043", **env)
    assert _policies(pg_db) == set()
    assert set(_rls_flags(pg_db).values()) == {(False, False)}
    assert q_app(pg_db, "SELECT count(*) FROM ping_hosts")[0][0] == 2

    _alembic(pg_db, "upgrade", "head", **env)
    _assert_rls_everywhere(pg_db)
    _assert_isolation(pg_db)


def test_existing_single_user_install_is_unchanged(pg_db):
    """Every install before phase 7: one DATABASE_URL (the owner, here a
    superuser like the Compose stack's POSTGRES_USER), no NODEGLOW_RLS."""
    _alembic(pg_db, "upgrade", "038")
    _seed(pg_db)
    _alembic(pg_db, "upgrade", "head")
    _run(["migrate.py"], pg_db)
    # Prepared, not switched on.
    assert set(_rls_flags(pg_db).values()) == {(False, False)}
    assert _policies(pg_db) == set(_live_tenant_tables())
    privileged = q(pg_db, "SELECT has_table_privilege($1, 'ping_hosts', 'SELECT')", APP_ROLE)
    assert privileged[0][0] is True
    # The single user reads everything without any setting, as before.
    assert q(pg_db, "SELECT count(*) FROM ping_hosts")[0][0] == 2
    _alembic(pg_db, "check")


LIMITED_SCRIPT = r"""
import asyncio, json
from models.base import create_owner_engine
from services import pg_rls

# A role name that cannot exist yet: CREATE ROLE is what must fail here.
pg_rls.APP_ROLE = "nodeglow_app_never_created"

async def main():
    engine = create_owner_engine()
    async with engine.begin() as conn:
        report = await conn.run_sync(lambda c: pg_rls.apply(
            c, ["ping_hosts", "incidents"], enabled=True, login=None))
    await engine.dispose()
    print(json.dumps({"app_role": report.app_role, "warnings": report.warnings,
                      "tables": report.tables}))

asyncio.run(main())
"""


def test_without_createrole_the_upgrade_warns_instead_of_failing(pg_db):
    """Managed PostgreSQL: the owner may not create roles. The setup logs
    it and goes on; the policies are created regardless."""
    import json

    from sqlalchemy.engine import make_url

    _run(["migrate.py"], pg_db)
    owner = f"ng_limited_{uuid.uuid4().hex[:6]}"
    q(pg_db, f"CREATE ROLE {owner} LOGIN PASSWORD 'limited' NOCREATEROLE NOSUPERUSER")
    try:
        for table in ("ping_hosts", "incidents"):
            q(pg_db, f"ALTER TABLE {table} OWNER TO {owner}")
        q(pg_db, f"GRANT USAGE ON SCHEMA public TO {owner}")
        limited = make_url(pg_db).set(username=owner, password="limited") \
            .render_as_string(hide_password=False)
        proc = _run(["-c", LIMITED_SCRIPT], limited)
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        assert report["app_role"] is False
        assert any("cannot create role" in w for w in report["warnings"]), report
        assert report["tables"] == ["ping_hosts", "incidents"]
        flags = _rls_flags(pg_db)
        assert flags["ping_hosts"] == (True, True) and flags["incidents"] == (True, True)
        assert {"ping_hosts", "incidents"} <= _policies(pg_db)
    finally:
        for table in ("ping_hosts", "incidents"):
            q(pg_db, f"ALTER TABLE {table} OWNER TO CURRENT_USER")
        q(pg_db, f"DROP OWNED BY {owner}")
        q(pg_db, f"DROP ROLE {owner}")


GUC_SCRIPT = r"""
import asyncio, json
from sqlalchemy import select, text
from models import AsyncSessionLocal, PingHost, engine
from services.tenancy import system_scope, tenant_scope

COUNT = text("SELECT count(*) FROM ping_hosts")

async def main():
    out = {}
    async with AsyncSessionLocal() as db:
        out["none"] = (await db.execute(COUNT)).scalar()
        with tenant_scope(1):
            out["t1"] = (await db.execute(COUNT)).scalar()
            # One transaction, several contexts: a lookup in system scope,
            # then on in the tenant.
            with system_scope("test: look across tenants"):
                out["system"] = (await db.execute(COUNT)).scalar()
            out["t1_again"] = (await db.execute(COUNT)).scalar()
            async with db.begin_nested() as sp:
                with tenant_scope(2):
                    out["t2_in_savepoint"] = (await db.execute(COUNT)).scalar()
                await sp.rollback()
            out["t1_after_savepoint"] = (await db.execute(COUNT)).scalar()
            db.add(PingHost(name="via-orm", hostname="10.9.9.9"))
            await db.commit()
            out["t1_orm"] = sorted(h.name for h in (await db.execute(select(PingHost))).scalars())
        out["none_after"] = (await db.execute(COUNT)).scalar()
    # A new transaction on a pooled connection starts clean again.
    async with AsyncSessionLocal() as db:
        out["fresh"] = (await db.execute(COUNT)).scalar()
    await engine.dispose()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_app_sets_the_tenant_per_transaction(pg_db):
    import json

    env = _rls_env(pg_db)
    _run(["migrate.py"], pg_db, **env)
    _seed_two_tenants(pg_db)
    proc = _run(["-c", GUC_SCRIPT], pg_db, **env)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {
        "none": 0, "t1": 1, "system": 2, "t1_again": 1, "t2_in_savepoint": 1,
        "t1_after_savepoint": 1, "t1_orm": ["host-a", "via-orm"], "none_after": 0, "fresh": 0,
    }


# Router tests that run unchanged against PostgreSQL as nodeglow_app with RLS
# on (tests/test_routers/conftest.py, "PostgreSQL mode").
ROUTER_TESTS_ON_PG = [
    "tests/test_routers/test_tenant_isolation_smoke.py",
    "tests/test_routers/test_tenant_auth_binding.py",
    "tests/test_routers/test_smoke.py",
    "tests/test_routers/test_tenancy_review_fixes.py",
    "tests/test_routers/test_api_v1.py",
    "tests/test_routers/test_dashboard_v2.py",
    # The phase-8 isolation walk (every route, session / API key / header,
    # reference check, licensed ee walk) as nodeglow_app with RLS on.
    "tests/test_routers/test_tenant_isolation.py",
    # Phase 9 (ee): lifecycle incl. the RLS check for the second tenant,
    # support access, and the purge of every tenant-owned table under RLS.
    "../ee/backend/tests/test_tenancy_lifecycle.py",
    "../ee/backend/tests/test_tenancy_support.py",
    "../ee/backend/tests/test_tenancy_purge.py",
    # Phase 9 hardening: shared identities, installation AI keys, support
    # access without IAM, default tenant plan, fail-closed retention cap.
    "../ee/backend/tests/test_tenancy_hardening.py",
]


def test_045_lifecycle_columns_and_support_grants(pg_db):
    """045: tenant lifecycle columns and support_grants (tenant-owned, with
    the isolation policy and RLS like every tenant table); down and up."""
    env = _rls_env(pg_db)
    _alembic(pg_db, "upgrade", "044", **env)
    _alembic(pg_db, "upgrade", "045", **env)
    cols = {r[0] for r in q(pg_db, "SELECT column_name FROM information_schema.columns "
                                   "WHERE table_name = 'tenants'")}
    assert {"suspended_at", "delete_after", "deletion_requested_at", "deletion_requested_by",
            "purge_started_at", "purge_state"} <= cols
    assert "support_grants" in _policies(pg_db)
    assert _rls_flags(pg_db)["support_grants"] == (True, True)
    q(pg_db, "INSERT INTO tenants (id, provider_id, name, slug, status, created_at) "
             "VALUES (2, 1, 'B', 'b', 'active', now())")
    q(pg_db, "INSERT INTO support_grants (tenant_id, role, reason, created_at, expires_at) "
             "VALUES (2, 'readonly', 'ticket', now(), now() + interval '1 hour')")
    assert q_app(pg_db, "SELECT count(*) FROM support_grants", tenant=1)[0][0] == 0
    assert q_app(pg_db, "SELECT count(*) FROM support_grants", tenant=2)[0][0] == 1
    _alembic(pg_db, "downgrade", "044", **env)
    assert "support_grants" not in _policies(pg_db)
    assert not q(pg_db, "SELECT 1 FROM information_schema.tables WHERE table_name = 'support_grants'")
    assert "delete_after" not in {r[0] for r in q(
        pg_db, "SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants'")}
    _alembic(pg_db, "upgrade", "head", **env)
    _alembic(pg_db, "check", **env)


def test_router_tests_against_postgres_with_rls(pg_db):
    _run(["migrate.py"], pg_db, **_rls_env(pg_db))
    template = pg_db.rsplit("/", 1)[1]
    proc = _run(
        ["-m", "pytest", "-q", "-p", "no:cacheprovider", *ROUTER_TESTS_ON_PG], pg_db,
        NODEGLOW_RLS="on",
        # The app's global engine stays on SQLite: a connection to the
        # template would block copying it.
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        NODEGLOW_TEST_PG_TEMPLATE=template,
        NODEGLOW_TEST_PG_ADMIN_URL=ADMIN_URL,
        NODEGLOW_TEST_PG_APP_URL=_app_dsn(pg_db),
    )
    assert " passed" in proc.stdout and " failed" not in proc.stdout, proc.stdout[-3000:]


RESTORE_TWO_USERS_SCRIPT = r"""
import asyncio, json, secrets
from datetime import datetime
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from models import AsyncSessionLocal, engine
from models.api_key import ApiKey
from routers.api_v1 import _hash_key
from services.tenancy import tenant_scope

async def main():
    raw = "ng_" + secrets.token_hex(16)
    with tenant_scope(1):
        async with AsyncSessionLocal() as db:
            # last_used now: the auth path writes nothing and does not commit,
            # so its transaction (locks on api_keys/tenants) stays open.
            db.add(ApiKey(name="ops", key_hash=_hash_key(raw), prefix=raw[:8], role="admin",
                          enabled=True, hash_algo="hmac", last_used=datetime.utcnow()))
            await db.commit()
    with patch("main.start_scheduler", new_callable=AsyncMock), patch("main.stop_scheduler"):
        from main import app
        headers = {"X-API-Key": raw}
        out = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/backup", json={"passphrase": "p" * 16}, headers=headers)
            assert r.status_code == 200, r.text
            envelope = r.json()
            for _ in range(2):
                r = await asyncio.wait_for(c.post(
                    "/api/v1/backup/restore", json={"backup": envelope, "passphrase": "p" * 16},
                    headers=headers), timeout=90)
                out.append([r.status_code, r.json().get("total_rows")])
    await engine.dispose()
    print(json.dumps(out))

asyncio.run(main())
"""


def test_api_key_restore_twice_with_two_users_and_rls(pg_db):
    """The restore's raw part runs on the owner connection. The request's
    own transaction (API-key lookup) must not block it: it hung forever on
    TRUNCATE api_keys before the request session was committed first."""
    import json

    env = _rls_env(pg_db)
    _run(["migrate.py"], pg_db, **env)
    _seed_two_tenants(pg_db)
    q(pg_db, "INSERT INTO settings (key, value, encrypted) VALUES ('setup_complete', 'true', false)")
    proc = _run(["-c", RESTORE_TWO_USERS_SCRIPT], pg_db, **env)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert [status for status, _ in out] == [200, 200], out
    assert all(rows and rows > 0 for _, rows in out)
    # The key and tenant B's host survived both restores (whole database).
    assert q(pg_db, "SELECT count(*) FROM api_keys")[0][0] == 1
    assert q(pg_db, "SELECT count(*) FROM ping_hosts WHERE name = 'CANARY-B'")[0][0] == 1


def test_update_step_never_switches_rls_on(pg_db):
    """The updater migrates while the old backend (maybe still NODEGLOW_RLS
    off) keeps serving: its migrate step must not enable RLS — the new
    backend's own start does. Keeping it on where it already is, is fine."""
    env = _rls_env(pg_db)
    _run(["migrate.py"], pg_db, **_rls_env(pg_db, flag="off"))
    _run(["migrate.py"], pg_db, NODEGLOW_RLS_DEFER_ENABLE="1", **env)
    assert set(_rls_flags(pg_db).values()) == {(False, False)}
    _run(["migrate.py"], pg_db, **env)  # the new backend's entrypoint
    _assert_rls_everywhere(pg_db)
    _run(["migrate.py"], pg_db, NODEGLOW_RLS_DEFER_ENABLE="1", **env)
    _assert_rls_everywhere(pg_db)


def test_app_role_cannot_create_objects_in_public(pg_db):
    """PostgreSQL < 15 (or upgraded clusters) grant CREATE on public to
    PUBLIC; the setup takes it away so the app role cannot plant objects
    the owner would resolve."""
    q(pg_db, "GRANT CREATE ON SCHEMA public TO PUBLIC")
    _run(["migrate.py"], pg_db, **_rls_env(pg_db))
    assert q(pg_db, "SELECT has_schema_privilege($1, 'public', 'CREATE')", APP_ROLE)[0][0] is False
    with pytest.raises(Exception, match="permission denied"):
        q_app(pg_db, "CREATE TABLE public.planted (x int)")
    # The owner still can (later migrations create tables).
    q(pg_db, "CREATE TABLE public.owner_made (x int)")
