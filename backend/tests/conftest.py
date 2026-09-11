"""Shared test fixtures – in-memory SQLite async database."""
import os
import sys

# Ensure backend/ is on the path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set env vars BEFORE importing any app modules
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(__file__), ".test_data"))

from datetime import datetime

import pytest
from sqlalchemy import DateTime, String, TypeDecorator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.base import Base


def _skip_pg_only(ddl, target, bind, **kw):
    """Skip PostgreSQL-specific DDL (GIN indexes, TSVECTOR columns) on SQLite."""
    return bind.dialect.name != "sqlite"


class StrictNaiveDateTime(TypeDecorator):
    """DateTime column type that rejects tz-aware bind values.

    Every time column in the application schema is TIMESTAMP WITHOUT TIME ZONE
    holding naive UTC, because the models default to ``datetime.utcnow``.
    SQLite happily compares such a column against a tz-aware value; asyncpg
    refuses it with a DataError. That divergence is invisible in this suite —
    the host timeline endpoint shipped four green tests while every production
    request returned 500. Making the mismatch fail here is the only way this
    class of bug gets caught before deploy.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is not None:
            raise AssertionError(
                f"tz-aware datetime bound to a naive TIMESTAMP column: {value!r}\n"
                "Postgres columns hold naive UTC — use datetime.utcnow() or "
                "strip tzinfo at the query boundary."
            )
        return value


def install_naive_datetime_guard(*bases):
    """Swap every DateTime column in the given declarative bases for the
    strict variant, so tz-aware comparisons fail loudly under SQLite."""
    for base in bases:
        for table in base.metadata.tables.values():
            for col in table.columns:
                if type(col.type) is DateTime:
                    col.type = StrictNaiveDateTime()


def pytest_sessionfinish(session, exitstatus):
    """Dispose the module-global async engine at session end.

    aiosqlite runs every connection in a *non-daemon* worker thread. Any code
    path that opens the global ``AsyncSessionLocal`` during a test (e.g. the
    real ``notifications.notify`` reading settings) connects this engine, and
    because it is never disposed the lingering worker thread blocks the
    interpreter's shutdown join — pytest hangs after the tests pass.
    """
    import asyncio

    from models.base import engine

    try:
        asyncio.run(engine.dispose())
    except Exception:
        pass


@pytest.fixture
async def db():
    """Provide a fresh in-memory SQLite database session per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Replace TSVECTOR columns with String for SQLite compatibility
    from sqlalchemy.dialects.postgresql import TSVECTOR
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, TSVECTOR):
                col.type = String()
        # Remove GIN indexes that SQLite can't handle
        table.indexes = {
            idx for idx in table.indexes
            if not getattr(idx, 'dialect_options', {}).get('postgresql', {}).get('using')
            and 'gin' not in str(getattr(idx, 'kwargs', {}))
        }

    install_naive_datetime_guard(Base)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Settings, users and sessions live in a second declarative Base in
        # database.py. Without it, anything reading a setting fails with
        # "no such table: settings" — and which tests hit that depended on
        # import order, so it surfaced only in certain run combinations.
        try:
            from database import Base as DbBase

            await conn.run_sync(DbBase.metadata.create_all)
        except ImportError:
            pass

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
