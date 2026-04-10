"""
Database engine, session factory, base class, and encryption helpers.
"""
import base64
import hashlib
from typing import AsyncGenerator

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import DATABASE_URL, SECRET_KEY


# Engine kwargs differ between SQLite and PostgreSQL
_engine_kwargs: dict = {"echo": False}
if DATABASE_URL.startswith("postgresql"):
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Encryption helpers ───────────────────────────────────────────────────────

# Static salt derived from SECRET_KEY itself — stable across restarts.
# A per-value random salt would be stronger but would break existing encrypted data.
_KDF_SALT = hashlib.sha256(("nodeglow-kdf-salt:" + SECRET_KEY).encode()).digest()[:16]


def _fernet() -> Fernet:
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
        yield session
