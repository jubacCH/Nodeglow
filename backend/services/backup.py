"""Backup/restore service — JSON-based PostgreSQL export/import."""
import base64
import json
import logging
import os
import zlib
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Tables to export (in dependency order — parents before children)
EXPORT_TABLES: list[str] = [
    "settings",
    "users",
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


async def export_backup(db: AsyncSession) -> dict:
    """Export all PostgreSQL tables as a JSON dict."""
    backup = {
        "_meta": {
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "format": "nodeglow-backup",
            # Never the key itself: credentials in this file are encrypted
            # with SECRET_KEY, which must be escrowed separately. The
            # fingerprint only tells a restore which key it needs.
            "secret_key_fingerprint": _key_fingerprint(),
        },
        "tables": {},
    }

    for table_name in EXPORT_TABLES:
        try:
            result = await db.execute(text(f"SELECT * FROM {_safe_table(table_name)}"))
            rows = result.mappings().all()
            backup["tables"][table_name] = [
                {k: _serialize(v) for k, v in dict(row).items()}
                for row in rows
            ]
        except Exception as e:
            logger.warning("Skipping table %s: %s", table_name, e)
            backup["tables"][table_name] = []

    return backup


async def get_backup_info(db: AsyncSession) -> dict:
    """Get database statistics for the backup UI."""
    tables = {}
    for table_name in EXPORT_TABLES:
        try:
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
    imported = {}

    key_warning = None
    backup_fp = meta.get("secret_key_fingerprint")
    current_fp = _key_fingerprint()
    if backup_fp and current_fp and backup_fp != current_fp:
        key_warning = (
            f"Backup was made with encryption key {backup_fp}, this installation "
            f"runs with {current_fp}: restored credentials will not decrypt until "
            "the original SECRET_KEY is configured."
        )
        logger.error(key_warning)

    # Disable FK checks during import
    await db.execute(text("SET session_replication_role = 'replica'"))

    try:
        # Truncate in reverse order (children first)
        for table_name in reversed(EXPORT_TABLES):
            if table_name in tables_data:
                await db.execute(text(f"TRUNCATE TABLE {_safe_table(table_name)} CASCADE"))

        # Get valid column names for each table from the DB schema
        _valid_columns: dict[str, set[str]] = {}
        conn = await db.connection()
        for t in EXPORT_TABLES:
            try:
                col_info = await conn.run_sync(lambda sc, tn=t: inspect(sc).get_columns(tn))
                _valid_columns[t] = {c["name"] for c in col_info}
            except Exception:
                _valid_columns[t] = set()

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
            rejected = set(rows[0].keys()) - allowed
            if rejected:
                logger.warning("Ignoring unknown columns in %s: %s", table_name, rejected)

            placeholders = ", ".join(f":{c}" for c in cols)
            col_names = ", ".join(f'"{c}"' for c in cols)

            for row in rows:
                # Only include validated columns
                safe_row = {c: row.get(c) for c in cols}
                await db.execute(
                    text(f"INSERT INTO {_safe_table(table_name)} ({col_names}) VALUES ({placeholders})"),
                    safe_row,
                )

            # Reset sequence
            try:
                safe = _safe_table(table_name)
                await db.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence(:tn, 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {safe}), 1))"
                    ),
                    {"tn": table_name},
                )
            except Exception:
                pass

            imported[table_name] = len(rows)

        await db.commit()
    finally:
        await db.execute(text("SET session_replication_role = 'origin'"))

    result = {"imported": imported, "total_rows": sum(imported.values())}
    if key_warning:
        result["warning"] = key_warning
    return result


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


def _serialize(value):
    """Convert Python values to JSON-serializable types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value
