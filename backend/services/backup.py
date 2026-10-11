"""Backup/restore service — JSON-based PostgreSQL export/import."""
import base64
import json
import logging
import os
import zlib
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from services import tenancy

logger = logging.getLogger(__name__)

# Tables to export (in dependency order — parents before children)
EXPORT_TABLES: list[str] = [
    "settings",
    "users",
    "tenant_memberships",
    "tenant_settings",
    "user_preferences",
    "ping_hosts",
    "ping_results",
    "integration_configs",
    "snapshots",
    "agents",
    "agent_snapshots",
    "incidents",
    "incident_events",
    "alert_rules",
    "log_templates",
    "host_baselines",
    "precursor_patterns",
    "credentials",
    "snmp_mibs",
    "snmp_oids",
    "snmp_host_configs",
    "snmp_results",
    "api_keys",
    "notification_logs",
    "discovered_ports",
    "audit_logs",
]

_ALLOWED_TABLES = frozenset(EXPORT_TABLES)


def _safe_table(name: str) -> str:
    """Validate table name against whitelist and return quoted identifier."""
    if name not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table: {name}")
    return f'"{name}"'


# The backup is installation-wide: every table, every tenant (operator-only,
# see routers/api_v1.py). The statements are raw SQL, which the session
# filter does not see; with row-level security on (services/pg_rls.py) they
# would return only the request's tenant — so they run in system scope.
_EXPORT_SCOPE = "backup: export every table of the installation"
_RESTORE_SCOPE = "backup: restore every table of the installation"
RESTORE_LOCK_TIMEOUT = "30s"


@asynccontextmanager
async def _restore_session(db: AsyncSession):
    """The session the restore's raw SQL runs in.

    TRUNCATE and ``session_replication_role`` are the owner's (the app role
    ``nodeglow_app`` has neither): with a separate owner connection
    (``DATABASE_OWNER_URL``) the restore uses it; with one database user,
    the request's own session, as before.
    """
    from models import base

    if base.DATABASE_OWNER_URL == base.DATABASE_URL:
        yield db
        return
    # The request's own transaction (API-key lookup, tenant check) still
    # holds ACCESS SHARE locks on api_keys/tenants: the owner's TRUNCATE
    # would wait for it forever — the waiter is this very coroutine, so
    # PostgreSQL sees no deadlock. Commit, not rollback: with
    # expire_on_commit=False the objects loaded so far (request.state.api_key,
    # read by the audit entry afterwards) stay usable.
    await db.commit()
    async with AsyncSession(base.owner_engine(), expire_on_commit=False) as owner_db:
        if owner_db.get_bind().dialect.name == "postgresql":
            # Any other lock holder makes the restore fail instead of hang
            # while it keeps the tables truncated so far locked.
            await owner_db.execute(text(f"SET LOCAL lock_timeout = '{RESTORE_LOCK_TIMEOUT}'"))
        yield owner_db


async def export_backup(db: AsyncSession) -> dict:
    """Export all PostgreSQL tables as a JSON dict.

    Tenant secrets (integration configs, credentials, secret tenant
    settings) are decrypted with their tenant's key and carried in
    plaintext (``services.tenant_keys.open_for_export``): tenant keys never
    leave the installation, and the restore encrypts them again under the
    key of the tenant they land in. The dict is therefore only ever handed
    out inside the passphrase-encrypted envelope (:func:`encrypt_backup`;
    the route refuses to export without a passphrase).
    """
    from services import tenant_keys

    backup = {
        "_meta": {
            "version": "1.1",
            "timestamp": datetime.utcnow().isoformat(),
            "format": "nodeglow-backup",
            # Never the key itself: installation secrets (settings) in this
            # file are encrypted with SECRET_KEY, which must be escrowed
            # separately. The fingerprint only tells a restore which key it
            # needs. Tenant secrets are plaintext inside the envelope.
            "secret_key_fingerprint": _key_fingerprint(),
            "tenant_secrets": "plaintext",
        },
        "tables": {},
    }

    with tenancy.system_scope(_EXPORT_SCOPE):
        for table_name in EXPORT_TABLES:
            try:
                # In a savepoint: on PostgreSQL a missing legacy table
                # (ping_results) would abort the transaction, and every
                # later table would be exported empty.
                async with db.begin_nested():
                    result = await db.execute(text(f"SELECT * FROM {_safe_table(table_name)}"))
                    rows = result.mappings().all()
                backup["tables"][table_name] = [
                    {k: _serialize(v) for k, v in dict(row).items()}
                    for row in rows
                ]
            except Exception as e:
                logger.warning("Skipping table %s: %s", table_name, e)
                backup["tables"][table_name] = []
        for table_name, rows in backup["tables"].items():
            await tenant_keys.open_for_export(db, table_name, rows)

    return backup


async def get_backup_info(db: AsyncSession) -> dict:
    """Get database statistics for the backup UI."""
    tables = {}
    with tenancy.system_scope(_EXPORT_SCOPE):
        for table_name in EXPORT_TABLES:
            try:
                async with db.begin_nested():
                    result = await db.execute(text(f"SELECT COUNT(*) FROM {_safe_table(table_name)}"))
                    count = result.scalar()
                tables[table_name] = count
            except Exception:
                tables[table_name] = 0

    # Total database size
    try:
        result = await db.execute(text(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ))
        db_size = result.scalar()
    except Exception:
        db_size = "unknown"

    return {
        "tables": tables,
        "total_rows": sum(tables.values()),
        "db_size": db_size,
    }


async def import_backup(db: AsyncSession, data: dict) -> dict:
    """Import a backup JSON dict, replacing all existing data."""
    meta = data.get("_meta", {})
    if meta.get("format") != "nodeglow-backup":
        raise ValueError("Invalid backup format")

    tables_data = data.get("tables", {})

    key_warning = None
    backup_fp = meta.get("secret_key_fingerprint")
    current_fp = _key_fingerprint()
    tenant_plain = meta.get("tenant_secrets") == "plaintext"
    if backup_fp and current_fp and backup_fp != current_fp:
        what = ("restored installation secrets (LDAP bind password, AI provider keys, "
                "licence)" if tenant_plain else "restored credentials")
        key_warning = (
            f"Backup was made with encryption key {backup_fp}, this installation "
            f"runs with {current_fp}: {what} will not decrypt until "
            "the original SECRET_KEY is configured."
        )
        logger.error(key_warning)

    from services import tenant_keys

    # Rows of a backup from before multi-tenancy (revision < 040) belong to
    # the importing tenant and its default site.
    tenant_id = tenancy.session_tenant_id(db)
    site_id = tenancy.backend().default_site_id(tenant_id)
    async with _restore_session(db) as raw:
        with tenancy.system_scope(_RESTORE_SCOPE):
            # Tenant secrets travel in plaintext inside the envelope: each is
            # encrypted under the key of the tenant its row lands in (keys
            # are created in the restore's transaction where missing).
            await tenant_keys.seal_for_restore(raw, tables_data, tenant_id)
            imported = await _restore_tables(raw, tables_data, tenant_id, site_id)

    # A backup from before the settings split (revision 042) carries the
    # tenant's settings in ``settings``: they belong to the importing tenant.
    from models.settings import adopt_legacy_tenant_settings

    # A restore replaces the data: the backup's values win, and the tenant's
    # current settings go (the backup had no tenant_settings to truncate them).
    tenant_id = tenancy.session_tenant_id(db)
    pre_042 = "settings" in tables_data and "tenant_settings" not in tables_data
    moved = await adopt_legacy_tenant_settings(db, tenant_id, replace=pre_042)
    # ...and from before the auth binding (043) only users.role was kept up
    # to date — it wins over whatever memberships came along (039's stale
    # copies) and decides is_operator; users without one get one, else
    # nobody could sign in after the restore.
    from services.memberships import backfill_memberships, sync_from_users_role

    user_rows = tables_data.get("users") or []
    pre_043 = bool(user_rows) and (
        "is_operator" not in user_rows[0] or not tables_data.get("tenant_memberships")
    )
    if pre_043:
        granted = await sync_from_users_role(db, tenant_id)
    else:
        granted = await backfill_memberships(db, tenant_id)
    if moved or granted:
        await db.commit()

    # A backup from before revision 046 (or one whose values could not be
    # opened) carries tenant secrets under the installation key: they move
    # to the tenant's key now, like 046 does on upgrade.
    restored_tenants = {
        int(r.get("tenant_id") or tenant_id)
        for t in ("integration_configs", "credentials", "tenant_settings")
        for r in (tables_data.get(t) or [])
    }
    moved_secrets = 0
    for tid in sorted(restored_tenants):
        with tenancy.system_scope(f"backup: restored secrets of tenant {tid} to its key"):
            moved_secrets += (await tenant_keys.reencrypt_legacy(db, tid)).converted
    if moved_secrets:
        await db.commit()

    result = {"imported": imported, "total_rows": sum(imported.values())}
    if key_warning:
        result["warning"] = key_warning
    return result


async def _table_exists(db: AsyncSession, table_name: str) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return True
    found = await db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table_name}"})
    return found.scalar() is not None


async def _restore_tables(raw: AsyncSession, tables_data: dict, tenant_id: int,
                          site_id: int | None) -> dict:
    """TRUNCATE and re-insert every table of the backup (raw SQL, system
    scope, owner connection — see :func:`_restore_session`)."""
    imported: dict = {}
    # Disable FK checks during import
    await raw.execute(text("SET session_replication_role = 'replica'"))

    try:
        # Truncate in reverse order (children first) — tables that exist:
        # an export lists the dropped legacy tables (ping_results) as empty,
        # and on PostgreSQL a failing TRUNCATE aborts the whole restore.
        for table_name in reversed(EXPORT_TABLES):
            if table_name in tables_data and await _table_exists(raw, table_name):
                await raw.execute(text(f"TRUNCATE TABLE {_safe_table(table_name)} CASCADE"))

        # Get valid column names for each table from the DB schema
        _valid_columns: dict[str, set[str]] = {}
        # Timestamp columns: the export writes them as ISO strings, and
        # asyncpg refuses a str for a TIMESTAMP parameter of raw SQL.
        _time_columns: dict[str, dict[str, type]] = {}
        conn = await raw.connection()
        from sqlalchemy import Date, DateTime
        for t in EXPORT_TABLES:
            try:
                col_info = await conn.run_sync(lambda sc, tn=t: inspect(sc).get_columns(tn))
                _valid_columns[t] = {c["name"] for c in col_info}
                _time_columns[t] = {
                    c["name"]: (DateTime if isinstance(c["type"], DateTime) else Date)
                    for c in col_info if isinstance(c["type"], (DateTime, Date))
                }
            except Exception:
                _valid_columns[t] = set()
                _time_columns[t] = {}

        # Insert in forward order (parents first)
        for table_name in EXPORT_TABLES:
            rows = tables_data.get(table_name, [])
            if not rows:
                imported[table_name] = 0
                continue

            # Validate column names against DB schema to prevent SQL injection
            allowed = _valid_columns.get(table_name, set())
            if not allowed:
                logger.warning("Skipping import of %s: could not determine schema", table_name)
                imported[table_name] = 0
                continue
            cols = [c for c in rows[0].keys() if c in allowed]
            if not cols:
                imported[table_name] = 0
                continue
            # A backup taken before multi-tenancy (revision < 040) has no
            # tenant_id/site_id: its rows belong to the importing tenant and
            # its default site. Raw SQL is not stamped by the session filter.
            defaults: dict[str, int | None] = {}
            if "tenant_id" in allowed and "tenant_id" not in rows[0]:
                defaults["tenant_id"] = tenant_id
            if "site_id" in allowed and "site_id" not in rows[0]:
                defaults["site_id"] = site_id
            cols += list(defaults)
            rejected = set(rows[0].keys()) - allowed
            if rejected:
                logger.warning("Ignoring unknown columns in %s: %s", table_name, rejected)

            placeholders = ", ".join(f":{c}" for c in cols)
            col_names = ", ".join(f'"{c}"' for c in cols)

            time_cols = _time_columns.get(table_name, {})
            for row in rows:
                # Only include validated columns
                safe_row = {c: row.get(c) for c in cols}
                for c, kind in time_cols.items():
                    if isinstance(safe_row.get(c), str):
                        safe_row[c] = _parse_time(safe_row[c], kind)
                safe_row.update(defaults)
                await raw.execute(
                    text(f"INSERT INTO {_safe_table(table_name)} ({col_names}) VALUES ({placeholders})"),
                    safe_row,
                )

            # Reset sequence — only where there is a serial id, and in a
            # savepoint: on PostgreSQL a failing statement (``settings`` has
            # no ``id``) aborts the whole transaction, which the except below
            # cannot undo, and every later INSERT of the restore failed.
            if "id" in allowed:
                try:
                    safe = _safe_table(table_name)
                    async with raw.begin_nested():
                        await raw.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence(:tn, 'id'), "
                                f"COALESCE((SELECT MAX(id) FROM {safe}), 1))"
                            ),
                            {"tn": table_name},
                        )
                except Exception:
                    pass

            imported[table_name] = len(rows)

        await raw.commit()
    finally:
        await raw.execute(text("SET session_replication_role = 'origin'"))
    return imported


# ── Encrypted export envelope ────────────────────────────────────────────────
#
# The plain export holds users (bcrypt hashes), API key hashes, settings and
# the (SECRET_KEY-encrypted) credentials. It is wrapped in a passphrase-
# encrypted envelope so a downloaded file is useless on its own:
#
#   {"format": "nodeglow-backup-encrypted", "version": 1,
#    "kdf": {"name": "scrypt", "n": .., "r": .., "p": .., "salt": b64},
#    "cipher": {"name": "AES-256-GCM", "nonce": b64},
#    "compression": "zlib", "created": iso8601, "ciphertext": b64}
#
# The header (everything but the ciphertext) is the GCM associated data, so
# tampering with the parameters fails authentication like a wrong passphrase.

ENVELOPE_FORMAT = "nodeglow-backup-encrypted"
ENVELOPE_VERSION = 1
MIN_PASSPHRASE_LENGTH = 12
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1   # ~32 MiB, ~0.1 s
_SALT_BYTES, _NONCE_BYTES = 16, 12


class BackupCryptoError(ValueError):
    """A backup could not be encrypted/decrypted. ``code`` is for the API."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def is_encrypted_backup(data) -> bool:
    return isinstance(data, dict) and data.get("format") == ENVELOPE_FORMAT


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def _aad(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def encrypt_backup(data: dict, passphrase: str) -> dict:
    """Wrap a plain export dict in the passphrase-encrypted envelope.

    CPU-bound (scrypt + compression): call via ``asyncio.to_thread``.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise BackupCryptoError(
            f"A backup passphrase of at least {MIN_PASSPHRASE_LENGTH} characters is required",
            "passphrase_required",
        )
    salt = os.urandom(_SALT_BYTES)
    nonce = os.urandom(_NONCE_BYTES)
    header = {
        "format": ENVELOPE_FORMAT,
        "version": ENVELOPE_VERSION,
        "kdf": {"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
                "salt": base64.b64encode(salt).decode()},
        "cipher": {"name": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode()},
        "compression": "zlib",
        "created": datetime.utcnow().isoformat(),
    }
    key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    plain = zlib.compress(json.dumps(data, separators=(",", ":")).encode(), 6)
    ct = AESGCM(key).encrypt(nonce, plain, _aad(header))
    return {**header, "ciphertext": base64.b64encode(ct).decode()}


def decrypt_backup(envelope: dict, passphrase: str | None) -> dict:
    """Open an encrypted envelope; raises :class:`BackupCryptoError`.

    CPU-bound: call via ``asyncio.to_thread``.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not passphrase:
        raise BackupCryptoError("This backup is encrypted: enter its passphrase", "passphrase_required")
    if envelope.get("version") != ENVELOPE_VERSION:
        raise BackupCryptoError(
            f"Unsupported encrypted backup version {envelope.get('version')!r}", "unsupported_version")
    try:
        kdf = envelope["kdf"]
        cipher = envelope["cipher"]
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        salt = base64.b64decode(kdf["salt"], validate=True)
        nonce = base64.b64decode(cipher["nonce"], validate=True)
        ct = base64.b64decode(envelope["ciphertext"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupCryptoError("Malformed encrypted backup", "invalid_backup") from exc
    # Bound the KDF cost a crafted file can make us pay (memory = 128*n*r).
    if (kdf.get("name") != "scrypt" or cipher.get("name") != "AES-256-GCM"
            or envelope.get("compression") != "zlib"
            or n < 2 ** 14 or n > 2 ** 20 or n & (n - 1) or not 1 <= r <= 16 or not 1 <= p <= 4
            or 128 * n * r > 256 * 1024 * 1024
            or not 16 <= len(salt) <= 64 or len(nonce) != _NONCE_BYTES):
        raise BackupCryptoError("Unsupported encryption parameters in backup", "invalid_backup")

    header = {k: v for k, v in envelope.items() if k != "ciphertext"}
    key = _derive_key(passphrase, salt, n, r, p)
    try:
        plain = AESGCM(key).decrypt(nonce, ct, _aad(header))
    except InvalidTag as exc:
        raise BackupCryptoError("Wrong passphrase or corrupted backup file", "decrypt_failed") from exc
    try:
        return json.loads(zlib.decompress(plain))
    except (zlib.error, ValueError) as exc:
        raise BackupCryptoError("Decrypted backup is not valid JSON", "invalid_backup") from exc


def _key_fingerprint() -> str | None:
    try:
        from config import secret_key_fingerprint
        return secret_key_fingerprint()
    except Exception:  # never let bookkeeping break a backup or restore
        logger.debug("could not compute the key fingerprint", exc_info=True)
        return None


def _parse_time(value: str, kind):
    """ISO string from an export back to datetime/date (naive UTC, as stored)."""
    from datetime import date
    from sqlalchemy import DateTime

    try:
        if kind is DateTime:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        return date.fromisoformat(value[:10])
    except ValueError:
        return value


def _serialize(value):
    """Convert Python values to JSON-serializable types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value
