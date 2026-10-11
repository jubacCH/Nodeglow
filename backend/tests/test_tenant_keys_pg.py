"""Revision 046 (per-tenant envelope encryption) against a real PostgreSQL.

Production safety: an install at 044 — RLS on, two database users — with
real-looking secrets in every secret-bearing table (integration configs,
credentials, encrypted and pre-encrypted tenant settings, plaintext channel
secrets of old installs, a value under a previous SECRET_KEY, installation
secrets) is upgraded. 046 itself rewrites no secret (the old backend still
serves while the updater migrates); the new backend's startup pass moves
them to the tenant keys. Every secret must decrypt to the same plaintext
through the app's own read paths at each step, as ``nodeglow_app`` with RLS
on. Also: re-running is a no-op, ``python -m services.tenant_keys verify``
reports counts without printing a secret, the downgrade puts everything
back under the installation key, and backups: the export decrypts nothing
(the wrapped tenant keys travel along, so the file needs SECRET_KEY as well
as its passphrase), and a two-tenant backup restores into an install that
has only tenant 1.

Needs ``NODEGLOW_PG_TEST_URL`` like tests/test_migrations_tenancy_pg.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.test_migrations_tenancy_pg import (  # noqa: F401 — pg_db is a fixture
    ADMIN_URL, BACKEND, _alembic, _app_dsn, _rls_env, _run, pg_db, q, q_app,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="NODEGLOW_PG_TEST_URL not set")

# Plaintexts; what each row holds before 046 is built from them below.
CONFIG_A = {"host": "https://pve.example:8006", "token_id": "root@pam!ng", "token_secret": "pve-s3cret"}
CONFIG_B = {"host": "https://unifi.b", "password": "unifi-b-secret"}
CRED_A = {"community": "snmp-community-a"}
DISCORD = "https://discord.com/api/webhooks/123/discord-token-xyz"
SMTP_PW = "smtp-pass-word"
TEAMS = "https://example.webhook.office.com/teams-secret"
WH_SECRET = "webhook-signing-secret"
TG_PLAIN = "123456:telegram-plaintext-from-an-old-install"
LDAP_PW = "ldap-bind-pw"
CLAUDE = "sk-ant-installation-key"
OLD_KEY_TOKEN = "gAAAAABwritten-under-a-previous-secret-key" + "A" * 40

ALL_SECRETS = (json.dumps(CONFIG_A), json.dumps(CONFIG_B), json.dumps(CRED_A), DISCORD, SMTP_PW,
               TEAMS, WH_SECRET, TG_PLAIN, LDAP_PW, CLAUDE, "pve-s3cret", "unifi-b-secret",
               "snmp-community-a", "discord-token-xyz")


def _seed_044(dsn: str) -> dict:
    """Secrets as an install at 044 stores them (installation key)."""
    from models.base import encrypt_value

    rows = {
        "cfg_a": encrypt_value(json.dumps(CONFIG_A)),
        "cfg_b": encrypt_value(json.dumps(CONFIG_B)),
        "cred_a": encrypt_value(json.dumps(CRED_A)),
        "discord": encrypt_value(DISCORD),
        "smtp": encrypt_value(SMTP_PW),
        "teams": encrypt_value(TEAMS),
        "whsec": encrypt_value(WH_SECRET),
        "ldap": encrypt_value(LDAP_PW),
        "claude": encrypt_value(CLAUDE),
    }
    for sql, *params in (
        ("INSERT INTO tenants (id, provider_id, name, slug, status, created_at) "
         "VALUES (2, 1, 'B', 'b', 'active', now())",),
        ("INSERT INTO integration_configs (id, tenant_id, type, name, config_json, enabled, created_at) "
         "VALUES (1, 1, 'proxmox', 'pve', $1, true, now()), (2, 2, 'unifi', 'unifi', $2, true, now())",
         rows["cfg_a"], rows["cfg_b"]),
        ("INSERT INTO credentials (id, tenant_id, name, type, data_json, created_at) "
         "VALUES (1, 1, 'snmp', 'snmp_v2c', $1, now()), (2, 1, 'stale', 'snmp_v2c', $2, now())",
         rows["cred_a"], OLD_KEY_TOKEN),
        ("INSERT INTO tenant_settings (tenant_id, key, value, encrypted) VALUES "
         "(1, 'discord_webhook_url', $1, false), (1, 'smtp_password', $2, true), "
         "(1, 'teams_webhook_url', $3, false), (1, 'webhook_secret', $4, false), "
         "(1, 'telegram_bot_token', $5, false), (1, 'site_name', 'ACME', false), "
         "(2, 'smtp_password', $2, true)",
         rows["discord"], rows["smtp"], rows["teams"], rows["whsec"], TG_PLAIN),
        ("INSERT INTO settings (key, value, encrypted) VALUES ('ldap_bind_password', $1, false), "
         "('claude_api_key', $2, false), ('setup_complete', 'true', false)",
         rows["ldap"], rows["claude"]),
    ):
        q(dsn, sql, *params)
    return rows


# Reads every secret through the app's own paths, as the app role with RLS on.
READ_SCRIPT = r"""
import asyncio, json
from sqlalchemy import select
from models import AsyncSessionLocal, engine
from models.credential import Credential
from models.integration import IntegrationConfig
from models.settings import get_setting
from services import tenant_keys
from services.integration import decrypt_config
from services.snmp import decrypt_credential
from services.tenancy import tenant_scope

async def main():
    out = {}
    for tid in (1, 2):
        with tenant_scope(tid):
            async with AsyncSessionLocal() as db:
                cipher = await tenant_keys.tenant_cipher(db)
                for cfg in (await db.execute(select(IntegrationConfig).order_by(IntegrationConfig.id))).scalars():
                    out[f"cfg:{cfg.name}"] = await decrypt_config(db, cfg)
                for cred in (await db.execute(select(Credential).order_by(Credential.id))).scalars():
                    try:
                        out[f"cred:{cred.name}"] = await decrypt_credential(db, cred)
                    except Exception as exc:
                        out[f"cred:{cred.name}"] = type(exc).__name__
                out[f"smtp:{tid}"] = await get_setting(db, "smtp_password")
                if tid == 1:
                    for k in ("discord_webhook_url", "teams_webhook_url", "webhook_secret",
                              "telegram_bot_token"):
                        out[k] = cipher.reveal(await get_setting(db, k, ""))
                    out["site_name"] = await get_setting(db, "site_name")
                    out["ldap"] = await get_setting(db, "ldap_bind_password")
                    out["claude"] = await get_setting(db, "claude_api_key")
    await engine.dispose()
    print(json.dumps(out))

asyncio.run(main())
"""

EXPECTED = {
    "cfg:pve": CONFIG_A, "cfg:unifi": CONFIG_B, "cred:snmp": CRED_A, "cred:stale": "InvalidToken",
    "smtp:1": SMTP_PW, "smtp:2": SMTP_PW, "discord_webhook_url": DISCORD, "teams_webhook_url": TEAMS,
    "webhook_secret": WH_SECRET, "telegram_bot_token": TG_PLAIN, "site_name": "ACME",
}


# The new backend's start (main.py lifespan), as the app role with RLS on.
STARTUP_SCRIPT = r"""
import asyncio
from models import engine
from services.tenant_keys import upgrade_legacy_secrets

async def main():
    print("moved", await upgrade_legacy_secrets())
    await engine.dispose()

asyncio.run(main())
"""


def _startup_pass(dsn: str, env: dict) -> int:
    out = _run(["-c", STARTUP_SCRIPT], dsn, **env).stdout.strip().splitlines()[-1]
    return int(out.split()[-1])


def _read(dsn: str, env: dict) -> dict:
    from models.base import decrypt_value

    out = json.loads(_run(["-c", READ_SCRIPT], dsn, **env).stdout.strip().splitlines()[-1])
    # Installation secrets are read by their callers with decrypt_value.
    out["ldap"], out["claude"] = decrypt_value(out["ldap"]), decrypt_value(out["claude"])
    return out


def _formats(dsn: str) -> dict:
    rows = {}
    for label, sql in (
        ("cfg", "SELECT name, config_json FROM integration_configs"),
        ("cred", "SELECT name, data_json FROM credentials"),
        ("ts", "SELECT tenant_id || ':' || key, value FROM tenant_settings"),
        ("set", "SELECT key, value FROM settings WHERE key IN ('ldap_bind_password', 'claude_api_key')"),
    ):
        for name, value in q(dsn, sql):
            rows[f"{label}:{name}"] = value
    return rows


def _verify(dsn: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("NODEGLOW_DISABLE_EE", "NODEGLOW_RLS", "DATABASE_OWNER_URL", "MIGRATION_DATABASE_URL"):
        env.pop(key, None)
    env["DATABASE_URL"] = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    env.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    env.setdefault("DATA_DIR", str(BACKEND / "tests" / ".test_data"))
    return subprocess.run([sys.executable, "-m", "services.tenant_keys", "verify"], cwd=BACKEND,
                          env=env, capture_output=True, text=True, timeout=120)


def test_046_keeps_every_secret_readable_with_rls_on(pg_db):
    env = _rls_env(pg_db)
    _alembic(pg_db, "upgrade", "044", **env)   # roles, grants, RLS on: production at 044
    before_rows = _seed_044(pg_db)
    before = _formats(pg_db)
    expected = {**EXPECTED, "ldap": LDAP_PW, "claude": CLAUDE}

    _alembic(pg_db, "upgrade", "head", **env)
    # 046 rewrites no secret: the old backend, still serving while the
    # updater migrates (or brought back by NODEGLOW_VERSION alone), reads
    # every value exactly as before — decrypt_value on each of them.
    from models.base import decrypt_value

    assert _formats(pg_db) == before
    for name in ("cfg:pve", "cfg:unifi", "cred:snmp", "ts:1:discord_webhook_url",
                 "ts:1:smtp_password", "ts:2:smtp_password", "ts:1:teams_webhook_url",
                 "ts:1:webhook_secret"):
        decrypt_value(before[name])
    assert [r[0] for r in q(pg_db, "SELECT tenant_id FROM tenant_keys")] == [1]
    assert _read(pg_db, env) == expected

    # The new backend's start moves them (as nodeglow_app, RLS on).
    assert _startup_pass(pg_db, env) == 9   # 8 under the installation key + 1 plaintext
    after = _formats(pg_db)

    # One key per tenant that has secrets, wrapped (never the raw key).
    keys = q(pg_db, "SELECT tenant_id, key_id, kek_id, wrapped_dek FROM tenant_keys ORDER BY tenant_id")
    assert [(r[0], r[2]) for r in keys] == [(1, "sk1"), (2, "sk1")]
    key_of = {r[0]: r[1] for r in keys}
    assert all(r[3] and r[3].startswith("gAAAAA") for r in keys)
    # Every tenant secret under the installation key is now v2 under its tenant's key...
    for name, tid in (("cfg:pve", 1), ("cfg:unifi", 2), ("cred:snmp", 1), ("ts:1:discord_webhook_url", 1),
                      ("ts:1:smtp_password", 1), ("ts:1:teams_webhook_url", 1),
                      ("ts:1:webhook_secret", 1), ("ts:2:smtp_password", 2)):
        assert after[name].startswith(f"v2:{key_of[tid]}:"), name
    # ...the plaintext channel secret of an old install is encrypted too...
    assert after["ts:1:telegram_bot_token"].startswith(f"v2:{key_of[1]}:")
    # ...a value under another key, non-secrets and installation secrets are untouched.
    for name in ("cred:stale", "ts:1:site_name", "set:ldap_bind_password", "set:claude_api_key"):
        assert after[name] == before[name], name
    assert before["cred:stale"] == OLD_KEY_TOKEN and before_rows["ldap"] == after["set:ldap_bind_password"]
    # No secret in clear anywhere.
    dump = json.dumps(after)
    for secret in ALL_SECRETS:
        assert secret not in dump, secret

    # Every secret still decrypts to the same plaintext, as nodeglow_app with RLS on.
    assert _read(pg_db, env) == expected
    # Row-level security covers tenant_keys: no setting, no keys.
    assert q_app(pg_db, "SELECT count(*) FROM tenant_keys")[0][0] == 0
    assert q_app(pg_db, "SELECT count(*) FROM tenant_keys", tenant=2)[0][0] == 1
    assert tuple(q(pg_db, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                          "WHERE relname = 'tenant_keys'")[0]) == (True, True)

    # Re-running (every start; an interrupted run resumed) changes nothing.
    _run(["migrate.py"], pg_db, **env)
    assert _startup_pass(pg_db, env) == 0
    assert _formats(pg_db) == after
    _alembic(pg_db, "check", **env)

    # verify: counts per format, the one undecryptable value, no secrets.
    v = _verify(pg_db)
    assert v.returncode == 1, v.stdout + v.stderr        # one decrypt failure
    out = v.stdout
    cfg_line = next(line for line in out.splitlines() if "integration_configs.config_json" in line)
    assert f"v2:{key_of[1]}=1" in cfg_line and f"v2:{key_of[2]}=1" in cfg_line
    assert "credentials.data_json: undecryptable=1, v2:" in out
    assert "plaintext=" not in out and "Decrypt failures: 1" in out
    for secret in ALL_SECRETS:
        assert secret not in out + v.stderr, secret

    # Downgrade: back under the installation key, same plaintext (what the
    # code of 044 reads: decrypt_value on every one of them).
    from models.base import decrypt_value

    _alembic(pg_db, "downgrade", "044", **env)
    down = _formats(pg_db)
    assert not any(isinstance(v, str) and v.startswith("v2:") for v in down.values())
    assert q(pg_db, "SELECT to_regclass('public.tenant_keys')")[0][0] is None
    plain = {
        "cfg:pve": json.dumps(CONFIG_A), "cfg:unifi": json.dumps(CONFIG_B),
        "cred:snmp": json.dumps(CRED_A), "ts:1:discord_webhook_url": DISCORD,
        "ts:1:smtp_password": SMTP_PW, "ts:2:smtp_password": SMTP_PW,
        "ts:1:teams_webhook_url": TEAMS, "ts:1:webhook_secret": WH_SECRET,
        "ts:1:telegram_bot_token": TG_PLAIN,
        "set:ldap_bind_password": LDAP_PW, "set:claude_api_key": CLAUDE,
    }
    assert {k: decrypt_value(down[k]) for k in plain} == plain
    for name in ("cred:stale", "ts:1:site_name"):
        assert down[name] == before[name], name

    # And up again: everything readable before and after the startup pass.
    _alembic(pg_db, "upgrade", "head", **env)
    assert _read(pg_db, env) == expected
    assert _startup_pass(pg_db, env) == 9
    assert _read(pg_db, env) == expected


EXPORT_SCRIPT = r"""
import asyncio, json, sys
from models import AsyncSessionLocal, engine
from services.backup import encrypt_backup, export_backup
from services.tenancy import tenant_scope

async def main():
    with tenant_scope(1):
        async with AsyncSessionLocal() as db:
            data = await export_backup(db)
    await engine.dispose()
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(encrypt_backup(data, "backup-passphrase-1"), fh)

asyncio.run(main())
"""

RESTORE_SCRIPT = r"""
import asyncio, json, sys
from models import AsyncSessionLocal, engine
from services.backup import decrypt_backup, import_backup
from services.tenancy import tenant_scope

async def main():
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = decrypt_backup(json.load(fh), "backup-passphrase-1")
    with tenant_scope(1):
        async with AsyncSessionLocal() as db:
            print(json.dumps(await import_backup(db, data)))
    await engine.dispose()

asyncio.run(main())
"""


@pytest.fixture
def pg_db2():
    import asyncio
    import uuid

    from tests.test_migrations_tenancy_pg import _admin, _asyncpg_dsn

    name = f"ng_tk_{uuid.uuid4().hex[:10]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    base = _asyncpg_dsn(ADMIN_URL).rsplit("/", 1)[0]
    yield f"{base}/{name}"
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _export(dsn: str, env: dict, path) -> dict:
    """Export as the app does; returns the opened envelope (what the
    passphrase alone gives whoever holds the file)."""
    from services.backup import decrypt_backup

    _run(["-c", EXPORT_SCRIPT, str(path)], dsn, **env)
    raw = path.read_text(encoding="utf-8")
    assert '"nodeglow-backup-encrypted"' in raw
    return decrypt_backup(json.loads(raw), "backup-passphrase-1")


def _restore(dsn: str, env: dict, path) -> dict:
    return json.loads(_run(["-c", RESTORE_SCRIPT, str(path)], dsn, **env).stdout.strip().splitlines()[-1])


def test_backups_decrypt_nothing_and_restore_two_tenants_into_a_one_tenant_install(
        pg_db, pg_db2, tmp_path):
    env = _rls_env(pg_db)
    _alembic(pg_db, "upgrade", "044", **env)
    _seed_044(pg_db)                                 # tenants 1 and 2
    _alembic(pg_db, "upgrade", "head", **env)
    env2 = _rls_env(pg_db2)
    _run(["migrate.py"], pg_db2, **env2)             # a fresh install: tenant 1 only
    assert [r[0] for r in q(pg_db2, "SELECT id FROM tenants")] == [1]

    # 1. Values still under the installation key (046 done, the new backend
    #    not started yet): the restore moves tenant 1's to its key and
    #    creates no key for tenant 2, which the target does not have.
    legacy_file = tmp_path / "legacy.json"
    _export(pg_db, env, legacy_file)
    result = _restore(pg_db2, env2, legacy_file)
    assert "tenant(s) 2" in result["warning"], result
    assert [r[0] for r in q(pg_db2, "SELECT DISTINCT tenant_id FROM tenant_keys")] == [1]
    t1_key = q(pg_db2, "SELECT key_id FROM tenant_keys WHERE tenant_id = 1")[0][0]
    restored = _formats(pg_db2)
    assert restored["cfg:pve"].startswith(f"v2:{t1_key}:")
    assert not restored["cfg:unifi"].startswith("v2:")       # tenant 2: left as it was
    out = json.loads(_run(["-c", READ_SCRIPT], pg_db2, **env2).stdout.strip().splitlines()[-1])
    assert out["cfg:pve"] == CONFIG_A and out["discord_webhook_url"] == DISCORD

    # 2. After the startup pass: the export carries v2 values and the wrapped
    #    keys, and nothing opened — the passphrase alone yields no secret.
    _startup_pass(pg_db, env)
    v2_file = tmp_path / "v2.json"
    opened = _export(pg_db, env, v2_file)
    dump = json.dumps(opened)
    for secret in ALL_SECRETS:
        assert secret not in dump, secret
    src_keys = sorted(tuple(r) for r in q(pg_db, "SELECT tenant_id, key_id, wrapped_dek FROM tenant_keys"))
    assert sorted((k["tenant_id"], k["key_id"], k["wrapped_dek"])
                  for k in opened["tables"]["tenant_keys"]) == src_keys
    result = _restore(pg_db2, env2, v2_file)
    assert "tenant(s) 2" in result["warning"], result
    assert sorted(tuple(r) for r in q(pg_db2, "SELECT tenant_id, key_id, wrapped_dek FROM tenant_keys")) \
        == src_keys
    out = json.loads(_run(["-c", READ_SCRIPT], pg_db2, **env2).stdout.strip().splitlines()[-1])
    assert out["cfg:pve"] == CONFIG_A and out["cred:snmp"] == CRED_A
    assert out["smtp:1"] == SMTP_PW and out["discord_webhook_url"] == DISCORD
    assert out["teams_webhook_url"] == TEAMS and out["webhook_secret"] == WH_SECRET
    assert out["telegram_bot_token"] == TG_PLAIN
    assert out["cred:stale"] == "InvalidToken"   # was unreadable at the source, still is

    # 3. The same file under another SECRET_KEY: the wrapped keys do not
    #    unwrap — the restore goes through and warns, nothing decrypts.
    other = {**env2, "SECRET_KEY": "a-completely-different-secret-key"}
    result = _restore(pg_db2, other, v2_file)
    assert "will not decrypt" in result["warning"]
    probe = _run(["-c", PROBE_SCRIPT], pg_db2, **other).stdout.strip().splitlines()[-1]
    assert probe == "TenantKeyUnavailable"


PROBE_SCRIPT = r"""
import asyncio
from sqlalchemy import select
from models import AsyncSessionLocal, engine
from models.integration import IntegrationConfig
from services.integration import decrypt_config
from services.tenancy import tenant_scope

async def main():
    with tenant_scope(1):
        async with AsyncSessionLocal() as db:
            cfg = (await db.execute(select(IntegrationConfig))).scalars().first()
            try:
                await decrypt_config(db, cfg)
                print("DECRYPTED")
            except Exception as exc:
                print(type(exc).__name__)
    await engine.dispose()

asyncio.run(main())
"""
