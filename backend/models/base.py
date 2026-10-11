"""
Database engine, session factory, base class, and encryption helpers.
"""
import base64
import functools
import hashlib
from typing import AsyncGenerator

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import DATABASE_OWNER_URL, DATABASE_URL, SECRET_KEY


# Engine kwargs differ between SQLite and PostgreSQL
_engine_kwargs: dict = {"echo": False}
if DATABASE_URL.startswith("postgresql"):
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def create_owner_engine():
    """A new engine on the owner connection (``DATABASE_OWNER_URL``), for
    migrations and other installation-wide work: no pool, and on PostgreSQL
    it starts in the row-level-security system scope (services.pg_rls).
    The caller disposes it."""
    from sqlalchemy.pool import NullPool

    from services.pg_rls import system_connect_args

    return create_async_engine(
        DATABASE_OWNER_URL, poolclass=NullPool,
        connect_args=system_connect_args(DATABASE_OWNER_URL),
    )


_owner_engine = None


def owner_engine():
    """The owner connection for runtime work that the app role may not do
    (the backup restore: TRUNCATE, ``session_replication_role``). The app's
    own engine when there is only one database user."""
    global _owner_engine
    if DATABASE_OWNER_URL == DATABASE_URL:
        return engine
    if _owner_engine is None:
        _owner_engine = create_owner_engine()
    return _owner_engine


class Base(DeclarativeBase):
    pass


# ── Encryption helpers ───────────────────────────────────────────────────────

# Static salt derived from SECRET_KEY itself — stable across restarts.
# A per-value random salt would be stronger but would break existing encrypted data.
_KDF_SALT = hashlib.sha256(("nodeglow-kdf-salt:" + SECRET_KEY).encode()).digest()[:16]


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    # 480k PBKDF2 rounds take ~50 ms. SECRET_KEY and the salt are fixed for the
    # life of the process, so the key is derived once instead of on every
    # encrypt/decrypt (the dashboard alone decrypted once per Proxmox cluster
    # per request).
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_KDF_SALT, iterations=480_000)
    key = kdf.derive(SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _fernet_legacy() -> Fernet:
    """Legacy key derivation (plain SHA256) for migration."""
    key = hashlib.sha256(SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        # Fall back to legacy key for old encrypted values
        return _fernet_legacy().decrypt(value.encode()).decode()


# ── Session dependency ───────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            # Roll back on error so the session is returned to the pool in a
            # clean state — otherwise the next pooled use raises PendingRollbackError.
            await session.rollback()
            raise
