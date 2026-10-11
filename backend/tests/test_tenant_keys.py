"""Per-tenant envelope encryption (services/tenant_keys.py, revision 046).

Wrap/unwrap, the cipher (formats, fail closed), the caches (per tenant, per
transaction), rotation, destroy (crypto-shred), isolation between tenants
(also with direct database access to the ciphertext and the wrapped keys),
the settings layer, the startup pass, backups (export opens, restore seals)
and revision 046's data step on SQLite. The PostgreSQL run of 046 is in
tests/test_tenant_keys_pg.py.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text

from models.base import decrypt_value, encrypt_value
from models.credential import Credential
from models.integration import IntegrationConfig
from models.tenant import Tenant, TenantKey, TenantSetting
from services import tenant_keys as tk
from services.tenancy import TenantContextError, system_scope, tenant_scope

A, B = 1, 2


async def _add_tenant_b(db):
    with system_scope("test: tenant B", audit=False):
        db.add(Tenant(id=B, provider_id=1, name="B", slug="b", status="active"))
        await db.commit()


def _session(db):
    """A second session on the same database, bound to nothing."""
    from sqlalchemy.ext.asyncio import AsyncSession

    return AsyncSession(db.bind, expire_on_commit=False)


# ── Primitives ───────────────────────────────────────────────────────────────

def test_wrap_unwrap_roundtrip():
    dek = tk.new_dek()
    wrapped = tk.wrap_dek(dek, 7, "abc123")
    assert dek not in wrapped.encode() and wrapped != dek.hex()
    assert tk.unwrap_dek(wrapped, 7, "abc123", tk.KEK_ID) == dek


def test_wrapped_key_is_bound_to_its_tenant_and_key_id():
    wrapped = tk.wrap_dek(tk.new_dek(), 7, "abc123")
    with pytest.raises(tk.TenantKeyError, match="does not belong"):
        tk.unwrap_dek(wrapped, 8, "abc123", tk.KEK_ID)
    with pytest.raises(tk.TenantKeyError, match="does not belong"):
        tk.unwrap_dek(wrapped, 7, "other", tk.KEK_ID)


def test_unwrap_needs_the_same_secret_key(monkeypatch):
    from models import base

    wrapped = tk.wrap_dek(tk.new_dek(), 1, "k1")
    tk._kek.cache_clear()
    monkeypatch.setattr(base, "installation_key_material", lambda: b"\x01" * 32)
    try:
        with pytest.raises(tk.TenantKeyError, match="SECRET_KEY"):
            tk.unwrap_dek(wrapped, 1, "k1", tk.KEK_ID)
    finally:
        tk._kek.cache_clear()


def test_kek_differs_from_the_installation_key():
    """A wrapped key does not open with the installation Fernet key, and an
    installation-key value does not open with the KEK."""
    wrapped = tk.wrap_dek(tk.new_dek(), 1, "k1")
    with pytest.raises(Exception):
        decrypt_value(wrapped)
    with pytest.raises(Exception):
        tk._kek(tk.KEK_ID).decrypt(encrypt_value("x").encode())


def test_unknown_kek_id():
    with pytest.raises(tk.TenantKeyError, match="unknown key-encryption key"):
        tk.unwrap_dek("x", 1, "k1", "kms-9")


def test_classify_never_returns_the_value():
    f = tk.dek_fernet(tk.new_dek())
    assert tk.classify("") == "empty"
    assert tk.classify(tk.seal(f, "k1", "s")) == "v2"
    assert tk.classify(encrypt_value("s")) == "legacy"
    assert tk.classify("gAAAAABnotourtoken" + "A" * 40) == "undecryptable"
    assert tk.classify("https://hooks.example/x") == "plaintext"


# ── Cipher ───────────────────────────────────────────────────────────────────

async def test_encrypt_creates_the_tenant_key_once(db):
    first = await tk.encrypt_secret(db, "s3cret")
    assert first.startswith("v2:")
    second = await tk.encrypt_secret(db, "other")
    assert tk.parse_v2(first)[0] == tk.parse_v2(second)[0]
    await db.commit()
    with system_scope("test"):
        rows = (await db.execute(select(TenantKey))).scalars().all()
    assert [(r.tenant_id, r.kek_id) for r in rows] == [(A, tk.KEK_ID)]
    assert await tk.decrypt_secret(db, first) == "s3cret"


async def test_reading_creates_no_key(db):
    cipher = await tk.tenant_cipher(db)
    assert cipher.key_id is None
    with pytest.raises(tk.TenantKeyError, match="no data key"):
        cipher.encrypt("x")
    with system_scope("test"):
        assert (await db.execute(select(TenantKey))).first() is None


async def test_values_from_before_tenant_keys_still_read(db):
    legacy = encrypt_value("old")
    assert await tk.decrypt_secret(db, legacy) == "old"
    cipher = await tk.tenant_cipher(db)
    assert cipher.reveal(legacy) == "old"
    assert cipher.reveal("plain-legacy") == "plain-legacy"
    assert cipher.reveal("") == "" and cipher.reveal(None) == ""
    with pytest.raises(Exception):
        cipher.decrypt("plain-legacy")


async def test_v2_failures_never_pass_through(db):
    stored = await tk.encrypt_secret(db, "s3cret")
    cipher = await tk.tenant_cipher(db)
    tampered = stored[:-6] + "AAAAAA"
    with pytest.raises(tk.TenantKeyUnavailable):
        cipher.decrypt(tampered)
    with pytest.raises(tk.TenantKeyUnavailable):
        cipher.reveal(tampered)          # not handed out as "plaintext"
    with pytest.raises(tk.TenantKeyUnavailable):
        cipher.reveal("v2:nosuchkey:" + stored.split(":", 2)[2])
    with pytest.raises(tk.TenantKeyUnavailable, match="malformed"):
        cipher.decrypt("v2:broken")
    import notification_channels as nc
    with pytest.raises(tk.TenantKeyError):
        nc.reveal(tampered, cipher.decrypt)
    from services.channel_secrets import reveal
    with pytest.raises(tk.TenantKeyError):
        reveal(stored)                   # a tenant value without the cipher


async def test_get_dek(db):
    key = await tk.get_dek(db, A)
    assert key.tenant_id == A and key.key_id
    again = await tk.get_dek(db, A)
    assert again.key_id == key.key_id
    with pytest.raises(tk.TenantKeyError):
        await tk.get_dek(_session(db), 99, create=False)


# ── Isolation ────────────────────────────────────────────────────────────────

async def test_a_tenant_context_cannot_use_another_tenants_key(db):
    await _add_tenant_b(db)
    s = _session(db)
    with tenant_scope(A):
        with pytest.raises(TenantContextError):
            await tk.tenant_cipher(s, B)
        with pytest.raises(TenantContextError):
            await tk.encrypt_secret(s, "x", tenant_id=B)
        with pytest.raises(TenantContextError):
            await tk.get_dek(s, B)
    await s.close()


async def test_keys_and_cache_are_per_tenant(db):
    """Same key id in both tenants (an attacker's choice, or a copied row):
    each tenant gets its own DEK; B's value does not open with A's key."""
    await _add_tenant_b(db)
    s = _session(db)
    with system_scope("test: two tenants, same key id", audit=False):
        for tid in (A, B):
            s.add(TenantKey(tenant_id=tid, key_id="same", kek_id=tk.KEK_ID,
                            wrapped_dek=tk.wrap_dek(tk.new_dek(), tid, "same")))
        await s.commit()
        ca, cb = await tk.tenant_cipher(s, A), await tk.tenant_cipher(s, B)
    a_val, b_val = ca.encrypt("for-a"), cb.encrypt("for-b")
    assert tk.parse_v2(a_val)[0] == tk.parse_v2(b_val)[0] == "same"
    assert ca.decrypt(a_val) == "for-a" and cb.decrypt(b_val) == "for-b"
    with pytest.raises(tk.TenantKeyUnavailable):
        ca.decrypt(b_val)
    with pytest.raises(tk.TenantKeyUnavailable):
        cb.decrypt(a_val)
    assert {k[0] for k in tk._unwrapped} == {A, B}
    await s.close()


async def test_b_ciphertext_copied_into_a_does_not_decrypt(db):
    """Direct database access: B's ciphertext written into A's row, and even
    B's wrapped key copied into A's key table, give A nothing."""
    await _add_tenant_b(db)
    s = _session(db)
    with tenant_scope(B):
        b_secret = await tk.encrypt_secret(s, json.dumps({"password": "b-only"}))
        s.add(IntegrationConfig(type="proxmox", name="b", config_json=b_secret))
        await s.commit()
    with tenant_scope(A):
        await tk.encrypt_secret(s, "a")  # A has a key of its own
        s.add(IntegrationConfig(type="proxmox", name="a", config_json=b_secret))
        await s.commit()
        cfg = (await s.execute(select(IntegrationConfig))).scalars().one()
        from services.integration import decrypt_config
        with pytest.raises(tk.TenantKeyUnavailable):
            await decrypt_config(s, cfg)
    with system_scope("test: copy B's wrapped key into A", audit=False):
        b_key = (await s.execute(select(TenantKey).where(TenantKey.tenant_id == B))).scalar_one()
        s.add(TenantKey(tenant_id=A, key_id=b_key.key_id, kek_id=b_key.kek_id,
                        wrapped_dek=b_key.wrapped_dek))
        await s.commit()
    with tenant_scope(A):
        with pytest.raises(tk.TenantKeyUnavailable, match="does not belong"):
            await decrypt_config(s, cfg)
    await s.close()


async def test_tenant_keys_rows_are_filtered_like_every_tenant_table(db):
    await _add_tenant_b(db)
    s = _session(db)
    with tenant_scope(B):
        await tk.encrypt_secret(s, "b")
        await s.commit()
    with tenant_scope(A):
        assert (await s.execute(select(TenantKey))).first() is None
    with pytest.raises(TenantContextError):
        await s.execute(select(TenantKey))
    await s.close()


# ── Rotation ─────────────────────────────────────────────────────────────────

async def _seed_secrets(db, tenant_id=A):
    with tenant_scope(tenant_id):
        cipher = await tk.tenant_cipher(db, write=True)
        db.add(IntegrationConfig(type="proxmox", name=f"p{tenant_id}",
                                 config_json=cipher.encrypt(json.dumps({"token": "t"}))))
        db.add(Credential(name=f"c{tenant_id}", type="snmp_v2c",
                          data_json=encrypt_value(json.dumps({"community": "pub"}))))
        db.add(TenantSetting(key="smtp_password", value=encrypt_value("smtp"), encrypted=False))
        db.add(TenantSetting(key="webhook_url", value="https://plain.example/hook"))
        db.add(TenantSetting(key="site_name", value="not a secret"))
        await db.commit()


async def _secret_values(s, tenant_id=A) -> dict:
    with tenant_scope(tenant_id):
        cipher = await tk.tenant_cipher(s)
        out = {}
        for row, attr, label in await tk._secret_rows(s, tenant_id):
            value = getattr(row, attr)
            out[f"{label}:{getattr(row, 'key', getattr(row, 'name', ''))}"] = (
                tk.parse_v2(value)[0] if tk.is_v2(value) else "legacy", cipher.reveal(value))
        return out


async def test_rotate_reencrypts_everything_and_retires_the_old_key(db):
    await _seed_secrets(db)
    s = _session(db)
    with tenant_scope(A):
        old = (await tk.get_dek(s)).key_id
        stale = (await tk.tenant_cipher(s)).encrypt("written with the old key")
        report = await tk.rotate_tenant_key(s, A)
        await s.commit()
    assert report.key_id != old and report.retired == [old]
    assert report.converted == 4 and not report.failed   # 1 v2 + 2 legacy + 1 plaintext
    values = await _secret_values(s)
    assert {k for k, _ in values.values()} == {report.key_id}
    assert {v for _, v in values.values()} == {json.dumps({"token": "t"}),
                                               json.dumps({"community": "pub"}),
                                               "smtp", "https://plain.example/hook"}
    with tenant_scope(A):
        # A value written concurrently with the old key is not lost.
        assert await tk.decrypt_secret(s, stale) == "written with the old key"
        assert (await tk.get_dek(s)).key_id == report.key_id
        site = (await s.execute(select(TenantSetting).where(TenantSetting.key == "site_name"))).scalar_one()
        assert site.value == "not a secret"
    await s.close()


async def test_rotation_rolls_back_cleanly(db):
    await _seed_secrets(db)
    s = _session(db)
    with tenant_scope(A):
        old = (await tk.get_dek(s)).key_id
        await s.commit()
        before = await _secret_values(s)
        await tk.rotate_tenant_key(s, A)
        await s.rollback()
        assert (await tk.get_dek(s, create=False)).key_id == old
    assert await _secret_values(s) == before
    await s.close()


# ── Destroy (crypto-shred) ───────────────────────────────────────────────────

async def test_destroy_fails_closed(db):
    await _add_tenant_b(db)
    await _seed_secrets(db, A)
    await _seed_secrets(db, B)
    s = _session(db)
    with tenant_scope(A):
        a_values = await _secret_values(s, A)
    with system_scope("test: purge tenant B"):
        assert await tk.destroy_tenant_key(s, B) == 1
        await s.commit()
    with system_scope("test"):
        keys = (await s.execute(select(TenantKey).where(TenantKey.tenant_id == B))).scalars().all()
    assert [(k.wrapped_dek, k.destroyed_at is not None) for k in keys] == [(None, True)]
    assert not any(k[0] == B for k in tk._unwrapped)

    s2 = _session(db)
    with tenant_scope(B):
        rows = await tk._secret_rows(s2, B)
        # Values still under the installation key were moved to the tenant
        # key first, so they are shredded too — nothing of B decrypts.
        assert rows and all(tk.is_v2(getattr(r, a)) for r, a, _ in rows)
        cipher = await tk.tenant_cipher(s2)
        for row, attr, _label in rows:
            with pytest.raises(tk.TenantKeyDestroyed, match="crypto-shred"):
                cipher.decrypt(getattr(row, attr))
            with pytest.raises(tk.TenantKeyDestroyed):
                cipher.reveal(getattr(row, attr))
        with pytest.raises(tk.TenantKeyDestroyed):
            cipher.encrypt("new")
        with pytest.raises(tk.TenantKeyDestroyed):
            await tk.encrypt_secret(s2, "new")
        with pytest.raises(tk.TenantKeyDestroyed):
            await tk.rotate_tenant_key(s2, B)
        cfg = (await s2.execute(select(IntegrationConfig))).scalars().one()
        from services.integration import decrypt_config
        with pytest.raises(tk.TenantKeyDestroyed):
            await decrypt_config(s2, cfg)
    # A is untouched.
    assert await _secret_values(s2, A) == a_values
    await s.close()
    await s2.close()


async def test_destroy_is_seen_by_the_next_transaction_of_another_session(db):
    stored = await tk.encrypt_secret(db, "x")
    await db.commit()
    other = _session(db)
    with tenant_scope(A):
        assert await tk.decrypt_secret(other, stored) == "x"
        with system_scope("test: destroy"):
            await tk.destroy_tenant_key(db, A)
            await db.commit()
        # Same transaction of `other`: its key set is memoised...
        assert await tk.decrypt_secret(other, stored) == "x"
        await other.commit()
        # ...the next one reads the tombstone.
        with pytest.raises(tk.TenantKeyDestroyed):
            await tk.decrypt_secret(other, stored)
    await other.close()


async def test_get_setting_of_a_destroyed_tenant_raises(db):
    from models.settings import get_setting, set_setting

    await set_setting(db, "smtp_password", "pw", encrypted=True)
    assert await get_setting(db, "smtp_password") == "pw"
    with system_scope("test: destroy"):
        await tk.destroy_tenant_key(db, A)
        await db.commit()
    with pytest.raises(tk.TenantKeyDestroyed):
        await get_setting(db, "smtp_password")


# ── Settings layer ───────────────────────────────────────────────────────────

async def test_encrypted_tenant_settings_use_the_tenant_key(db):
    from models.settings import Setting, get_setting, set_setting

    await set_setting(db, "smtp_password", "pw", encrypted=True)
    row = await db.get(TenantSetting, (A, "smtp_password"))
    assert row.encrypted and tk.is_v2(row.value)
    assert await get_setting(db, "smtp_password") == "pw"
    # A pre-046 row (installation key) reads as before.
    row.value = encrypt_value("legacy-pw")
    await db.commit()
    assert await get_setting(db, "smtp_password") == "legacy-pw"
    # Installation keys keep the installation key.
    await set_setting(db, "ldap_bind_password", "bind", encrypted=True)
    inst = await db.get(Setting, "ldap_bind_password")
    assert not tk.is_v2(inst.value) and decrypt_value(inst.value) == "bind"
    assert await get_setting(db, "ldap_bind_password") == "bind"


async def test_smtp_fallback_to_the_installation_relay(db):
    """smtp_password falls back to the installation row (installation key)."""
    from models.settings import Setting

    db.add(Setting(key="smtp_password", value=encrypt_value("relay"), encrypted=False))
    await db.commit()
    from models.settings import get_setting
    cipher = await tk.tenant_cipher(db)
    assert cipher.reveal(await get_setting(db, "smtp_password")) == "relay"


# ── Startup pass, verify ─────────────────────────────────────────────────────

async def test_startup_pass_moves_legacy_values_and_is_idempotent(db):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await _seed_secrets(db)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    assert await tk.upgrade_legacy_secrets(factory) == 3   # 2 legacy + 1 plaintext setting
    assert await tk.upgrade_legacy_secrets(factory) == 0
    s = _session(db)
    values = await _secret_values(s)
    assert "legacy" not in {k for k, _ in values.values()}
    async with factory() as s3:
        report = await tk.scan(s3)
    assert report["failures"] == 0
    assert set(report["columns"]["credentials.data_json"]) == {f"v2:{(await _key(s))}"}
    await s.close()


async def _key(s):
    with tenant_scope(A):
        return (await tk.get_dek(s, create=False)).key_id


async def test_tenant_without_secrets_gets_no_key_from_the_startup_pass(db):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    assert await tk.upgrade_legacy_secrets(factory) == 0
    with system_scope("test"):
        assert (await db.execute(select(TenantKey))).first() is None


async def test_scan_reports_formats_and_failures_without_secrets(db):
    await _seed_secrets(db)
    with tenant_scope(A):
        db.add(Credential(name="old-key", type="snmp_v2c", data_json="gAAAAABold" + "A" * 60))
        await db.commit()
    report = await tk.scan(_session(db))
    text_report = tk._format_report(report)
    assert report["failures"] == 1
    assert report["columns"]["credentials.data_json"].get("legacy") == 1
    assert report["columns"]["credentials.data_json"].get("undecryptable") == 1
    assert report["columns"]["tenant_settings.value"].get("plaintext") == 1
    for secret in ("smtp", "pub", "https://plain.example/hook", '"token"'):
        assert secret not in text_report and secret not in json.dumps(report)


# ── Backups ──────────────────────────────────────────────────────────────────

async def test_export_opens_and_restore_seals_under_the_target_key(db):
    await _seed_secrets(db)
    from services.backup import export_backup
    s = _session(db)
    with tenant_scope(A):
        data = await export_backup(s)
    assert data["_meta"]["tenant_secrets"] == "plaintext"
    tables = data["tables"]
    assert "tenant_keys" not in tables
    cfg = tables["integration_configs"][0]["config_json"]
    assert tk.is_plain_marker(cfg) and json.loads(cfg["value"]) == {"token": "t"}
    assert tk.is_plain_marker(tables["credentials"][0]["data_json"])
    by_key = {r["key"]: r["value"] for r in tables["tenant_settings"]}
    assert by_key["smtp_password"]["value"] == "smtp"
    assert by_key["site_name"] == "not a secret"
    # Exported only inside the passphrase envelope; it round-trips.
    from services.backup import decrypt_backup, encrypt_backup
    restored = decrypt_backup(encrypt_backup(data, "a" * 16), "a" * 16)
    assert restored["tables"]["credentials"] == tables["credentials"]

    # Restore into another installation: its own tenant key.
    with system_scope("test: restore"):
        target_key = (await tk.get_dek(s, A)).key_id
        sealed = await tk.seal_for_restore(s, restored["tables"], A)
    # config, credential, smtp_password; the plaintext webhook_url row was
    # exported as stored (the restore's legacy pass encrypts it).
    assert sealed == 3
    cfg_value = restored["tables"]["integration_configs"][0]["config_json"]
    assert tk.parse_v2(cfg_value)[0] == target_key
    with tenant_scope(A):
        assert json.loads(await tk.decrypt_secret(s, cfg_value)) == {"token": "t"}
    await s.close()


async def test_restore_refuses_plaintext_markers_outside_secret_columns(db):
    tables = {"ping_hosts": [{"tenant_id": 1, "name": {tk.SECRET_MARKER: "plain", "value": "x"}}]}
    with system_scope("test"):
        with pytest.raises(ValueError, match="plaintext secret"):
            await tk.seal_for_restore(db, tables, A)


# ── Revision 046's data step (SQLite; PostgreSQL in test_tenant_keys_pg.py) ──

def _load_046():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "046_tenant_envelope_keys.py"
    spec = importlib.util.spec_from_file_location("rev046", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_revision_046_data_step_is_idempotent_and_reversible(db):
    rev = _load_046()
    assert set(rev.SECRET_SETTING_KEYS) == tk._secret_setting_keys(), \
        "frozen list in 046 must match the registry at this revision"
    await _seed_secrets(db)
    with tenant_scope(A):
        db.add(TenantSetting(key="smtp_user", value=encrypt_value("flagged"), encrypted=True))
        db.add(Credential(name="old-key", type="snmp_v2c", data_json="gAAAAABold" + "A" * 60))
        await db.commit()
    s = _session(db)
    before = await _secret_values(s)
    async with db.bind.connect() as conn:
        up = await conn.run_sync(rev.migrate_up)
        again = await conn.run_sync(rev.migrate_up)
        await conn.commit()
    assert up["integration_configs.config_json"] == {"converted": 0, "skipped": 1, "failed": 0}
    assert up["credentials.data_json"] == {"converted": 1, "skipped": 0, "failed": 1}
    # smtp_password (legacy) and smtp_user (flagged) move; the plaintext
    # webhook_url is left for the startup pass.
    assert up["tenant_settings.value"] == {"converted": 2, "skipped": 0, "failed": 1}
    assert all(c["converted"] == 0 for c in again.values())
    await s.commit()
    after = await _secret_values(s)
    assert {k: v for k, (_f, v) in after.items()} == {k: v for k, (_f, v) in before.items()}
    from models.settings import get_setting
    assert await get_setting(db, "smtp_user") == "flagged"
    with system_scope("test"):
        old = (await s.execute(select(Credential).where(Credential.name == "old-key"))).scalar_one()
    assert old.data_json.startswith("gAAAAABold")  # left exactly as it was

    async with db.bind.connect() as conn:
        down = await conn.run_sync(rev.migrate_down)
        await conn.commit()
    assert down["credentials.data_json"]["converted"] == 1
    await s.commit()
    with system_scope("test"):
        rows = (await s.execute(text("SELECT data_json FROM credentials ORDER BY id"))).scalars().all()
    assert decrypt_value(rows[0]) == json.dumps({"community": "pub"})
    # Back under the installation key, same plaintext everywhere.
    assert {k: v for k, (_f, v) in (await _secret_values(s)).items()} == \
        {k: v for k, (_f, v) in before.items()}
    await s.close()


async def test_revision_046_creates_tenant_1_key_without_secrets(db):
    rev = _load_046()
    async with db.bind.connect() as conn:
        await conn.run_sync(rev.migrate_up)
        await conn.commit()
    with system_scope("test"):
        keys = (await db.execute(select(TenantKey.tenant_id))).scalars().all()
    assert keys == [A]


async def test_retired_and_destroyed_states_in_scan(db):
    await tk.encrypt_secret(db, "x")
    await db.commit()
    with system_scope("test"):
        await tk.rotate_tenant_key(db, A)
        await db.commit()
        report = await tk.scan(db)
    assert sorted(report["keys"][str(A)].values()) == ["active", "retired"]
    with system_scope("test"):
        await tk.destroy_tenant_key(db, A)
        await db.commit()
        report = await tk.scan(db)
    assert set(report["keys"][str(A)].values()) == {"destroyed"}
    assert report["columns"]["tenant_settings.value"] == {}
