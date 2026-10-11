"""Row-level security (services/pg_rls.py), the parts that need no server.

The per-transaction settings hook is driven with a fake connection: the
SQLite suite cannot run RLS, and the real behaviour is covered against
PostgreSQL in test_migrations_tenancy_pg.py.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

from services import pg_rls
from services.tenancy import SESSION_TENANT_KEY, system_scope, tenant_scope, tenant_tables


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.closed = False

    def execute(self, sql, *args):
        self.log.append(sql)

    def close(self):
        self.closed = True


class FakeDbapi:
    def __init__(self):
        self.executed: list[str] = []
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cur = FakeCursor(self.executed)
        self.cursors.append(cur)
        return cur


class FakeConn:
    """What the hook touches on a SQLAlchemy Connection: .info, .connection."""

    def __init__(self):
        self.info: dict = {}
        self.connection = FakeDbapi()

    def run(self, statement="SELECT 1"):
        pg_rls._before_cursor_execute(self, None, statement, (), None, False)
        return list(self.connection.executed)


def _settings(sql: str) -> tuple[str, str]:
    import re

    tenant, system = re.findall(r"set_config\('[^']+', '([^']*)', true\)", sql)
    return tenant, system


def test_wanted_settings_follow_the_tenant_context():
    assert pg_rls.wanted_settings(None, None) == ("", "off")
    assert pg_rls.wanted_settings(None, 3) == ("3", "off")
    with tenant_scope(7) as ctx:
        assert pg_rls.wanted_settings(ctx, 3) == ("7", "off")  # context beats binding
    with system_scope("test") as ctx:
        assert pg_rls.wanted_settings(ctx, 3) == ("", "on")


def test_set_config_sql_only_takes_what_the_hook_builds():
    sql = pg_rls.set_config_sql(("12", "off"))
    assert "set_config('nodeglow.tenant_id', '12', true)" in sql
    assert "set_config('nodeglow.system', 'off', true)" in sql
    for bad in (("1; DROP TABLE x", "off"), ("1", "yes"), ("'", "on")):
        with pytest.raises(ValueError):
            pg_rls.set_config_sql(bad)


def test_no_context_sets_nothing_and_stays_fail_closed():
    conn = FakeConn()
    assert conn.run() == []  # a fresh transaction already reads no tenant


def test_settings_are_set_once_per_transaction_and_context():
    conn = FakeConn()
    with tenant_scope(1):
        conn.run()
        conn.run()
    assert [_settings(s) for s in conn.connection.executed] == [("1", "off")]
    assert all(c.closed for c in conn.connection.cursors)

    # Changing context inside the same transaction re-sets, both ways.
    with system_scope("token lookup"):
        conn.run()
    with tenant_scope(1):
        conn.run()
    conn.run()  # context left: back to nothing
    assert [_settings(s) for s in conn.connection.executed] == [
        ("1", "off"), ("", "on"), ("1", "off"), ("", "off"),
    ]


def test_transaction_end_forgets_what_was_set():
    conn = FakeConn()
    with tenant_scope(2):
        conn.run()
        for boundary in ("begin", "commit", "rollback"):
            pg_rls._transaction_boundary(conn)
            conn.run()
    assert len(conn.connection.executed) == 4, boundary


def test_rollback_to_savepoint_forces_a_reset():
    conn = FakeConn()
    with tenant_scope(2):
        conn.run()
    conn.run()                      # cleared again: ("", "off") set
    pg_rls._savepoint_rollback(conn)
    conn.run()                      # unknown → set even for "nothing"
    assert [_settings(s) for s in conn.connection.executed] == [
        ("2", "off"), ("", "off"), ("", "off"),
    ]


@pytest.mark.parametrize("statement", [
    "SAVEPOINT sa_savepoint_1", "RELEASE SAVEPOINT sa_savepoint_1",
    "ROLLBACK TO SAVEPOINT sa_savepoint_1", "  savepoint x",
])
def test_savepoint_statements_never_carry_settings(statement):
    # SQLAlchemy emits SAVEPOINT lazily with the first statement inside it;
    # a setting made right before it is what ROLLBACK TO would restore.
    conn = FakeConn()
    with tenant_scope(5):
        assert conn.run(statement) == []
        assert len(conn.run("SELECT 1")) == 1


def test_session_binding_reaches_the_connection():
    class Session:
        info = {SESSION_TENANT_KEY: 4}

    conn = FakeConn()
    pg_rls._after_begin(Session(), None, conn)
    conn.run()
    assert [_settings(s) for s in conn.connection.executed] == [("4", "off")]

    class Unbound:
        info: dict = {}

    pg_rls._after_begin(Unbound(), None, conn)
    conn.run()
    assert _settings(conn.connection.executed[-1]) == ("", "off")


def test_checkin_drops_the_per_connection_state():
    class Record:
        info = {pg_rls._STATE_KEY: ("1", "off"), pg_rls._BOUND_KEY: 1, "other": 1}

    record = Record()
    pg_rls._pool_clear(None, record)
    assert record.info == {"other": 1}
    pg_rls._pool_clear(None, None)  # invalidated connection: no record


def test_hooks_are_only_installed_on_postgresql():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert pg_rls.install_engine_hooks(engine) is False


def test_app_login_only_when_the_app_has_its_own_user():
    owner = "postgresql+asyncpg://nodeglow:ownerpw@db:5432/nodeglow"
    assert pg_rls.app_login(owner, owner) is None
    assert pg_rls.app_login("sqlite+aiosqlite:///x.db", "sqlite+aiosqlite:///x.db") is None
    login = pg_rls.app_login("postgresql+asyncpg://nodeglow_app:s%40cret@db:5432/nodeglow", owner)
    assert login == pg_rls.AppLogin("nodeglow_app", "s@cret")


def test_system_connect_args_only_for_asyncpg():
    assert pg_rls.system_connect_args("postgresql+asyncpg://u@h/db") == {
        "server_settings": {"nodeglow.system": "on"},
    }
    assert pg_rls.system_connect_args("sqlite+aiosqlite:///:memory:") == {}


def test_scram_verifier_has_the_postgres_format():
    import base64
    import hashlib
    import hmac

    salt = b"0123456789abcdef"
    v = pg_rls.scram_sha256_verifier("pencil", salt=salt)
    method, rest = v.split("$", 1)
    iterations_salt, keys = rest.split("$")
    iterations, salt_b64 = iterations_salt.split(":")
    stored_b64, server_b64 = keys.split(":")
    assert method == "SCRAM-SHA-256" and iterations == "4096"
    assert base64.b64decode(salt_b64) == salt
    # RFC 5802: StoredKey = H(HMAC(SaltedPassword, "Client Key")).
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    assert base64.b64decode(stored_b64) == hashlib.sha256(
        hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    assert base64.b64decode(server_b64) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    assert pg_rls.scram_sha256_verifier("pencil") != pg_rls.scram_sha256_verifier("pencil")


def test_quoting():
    assert pg_rls.quote_ident('we"ird') == '"we""ird"'
    assert pg_rls.quote_literal("it's") == "'it''s'"


def _status(**kw):
    base = {"user": "nodeglow_app", "superuser": False, "bypassrls": False,
            "owns_tables": 0, "tables": 29, "enabled": 29, "forced": 29}
    base.update(kw)
    return base


def test_assess_runtime_status():
    assert pg_rls.assess(_status(), True) == []
    assert pg_rls.assess(_status(enabled=0, forced=0), False) == []
    # On in the database, off in the app: every tenant table looks empty.
    [(level, msg)] = pg_rls.assess(_status(), False)
    assert level == logging.ERROR and "NODEGLOW_RLS is off" in msg
    [(level, msg)] = pg_rls.assess(_status(enabled=3, forced=3), True)
    assert level == logging.ERROR and "migrate.py" in msg
    [(level, msg)] = pg_rls.assess(_status(user="nodeglow", superuser=True), True)
    assert level == logging.WARNING and "superuser" in msg
    [(level, msg)] = pg_rls.assess(_status(user="nodeglow", owns_tables=29), True)
    assert level == logging.INFO


def test_policy_fails_closed_on_an_unset_tenant():
    # '' after a transaction that set it, NULL before: both must match nothing.
    assert "NULLIF(current_setting('nodeglow.tenant_id', true), '')::integer" in pg_rls.POLICY_EXPR
    assert "current_setting('nodeglow.system', true) = 'on'" in pg_rls.POLICY_EXPR


def test_revision_044_covers_every_tenant_table_of_its_time():
    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "044_row_level_security.py"
    spec = importlib.util.spec_from_file_location("rev044", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = set(module.TENANT_TABLES)
    live = {t.name for t in tenant_tables()}
    # Tables added later get their policy from migrate.py (live list);
    # nothing classified then may be missing from the revision.
    assert frozen <= live


def test_owner_url_resolution():
    from config import resolve_owner_url, same_database

    app = "postgresql+asyncpg://nodeglow:pw@db:5432/nodeglow"
    compose_owner = "postgresql+asyncpg://nodeglow:pw@db:5432/nodeglow"
    external = "postgresql+asyncpg://ng:pw@pg.example:5432/ng"
    # One user (every existing install): the app's URL, also when only
    # DATABASE_URL was overridden to another server.
    assert resolve_owner_url({"NODEGLOW_COMPOSE_OWNER_URL": compose_owner}, external) == external
    # Two users: the compose owner URL.
    env = {"NODEGLOW_COMPOSE_OWNER_URL": compose_owner, "POSTGRES_APP_USER": "nodeglow_app"}
    assert resolve_owner_url(env, app) == compose_owner
    # Explicit wins; blank does not count.
    assert resolve_owner_url({**env, "MIGRATION_DATABASE_URL": external}, app) == external
    assert resolve_owner_url({**env, "DATABASE_OWNER_URL": " "}, app) == compose_owner
    assert same_database(app, "postgresql+asyncpg://nodeglow_app:x@db/nodeglow")
    assert not same_database(app, external)
