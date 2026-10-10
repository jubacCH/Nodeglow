"""Shared fixtures for router smoke tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", ".test_data"))

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.responses import HTMLResponse

from models.base import Base as ModelsBase
from database import Base as DbBase
from tests.conftest import install_naive_datetime_guard


class FakeUser:
    id = 1
    username = "admin"
    role = "admin"


_AUTH_DISABLED = object()


@asynccontextmanager
async def make_client(fake_user=_AUTH_DISABLED):
    """Build an httpx AsyncClient wired to the FastAPI app.

    By default the session lookup is patched to always return ``FakeUser`` —
    auth is bypassed. Pass ``fake_user=None`` to keep the real cookie lookup
    (auth enabled), or any user-like object to be "logged in" as it.
    Yields ``(client, session_factory)``.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Ensure all models are registered with metadata before create_all
    import models.agent  # noqa: F401
    import models.scanner  # noqa: F401
    import models.syslog  # noqa: F401
    import models.credential  # noqa: F401
    import models.snmp  # noqa: F401

    install_naive_datetime_guard(DbBase, ModelsBase)

    # SQLite compat: replace TSVECTOR, remove GIN indexes
    from sqlalchemy.dialects.postgresql import TSVECTOR
    for base in (DbBase, ModelsBase):
        for table in base.metadata.tables.values():
            for col in table.columns:
                if isinstance(col.type, TSVECTOR):
                    col.type = String()
            table.indexes = {
                idx for idx in table.indexes
                if not getattr(idx, 'dialect_options', {}).get('postgresql', {}).get('using')
                and 'gin' not in str(getattr(idx, 'kwargs', {}))
            }

    async with engine.begin() as conn:
        await conn.run_sync(DbBase.metadata.create_all)
        await conn.run_sync(ModelsBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed minimal settings
    async with session_factory() as seed_db:
        from database import Setting
        seed_db.add(Setting(key="setup_complete", value="true"))
        seed_db.add(Setting(key="site_name", value="NODEGLOW"))
        seed_db.add(Setting(key="timezone", value="UTC"))
        seed_db.add(Setting(key="syslog_port", value="1514"))
        await seed_db.commit()

    @asynccontextmanager
    async def fake_session():
        async with session_factory() as session:
            yield session

    async def _ch_query_mock(sql, params=None):
        return []

    async def _ch_scalar_mock(sql, params=None):
        return 0

    # Mock Jinja2 TemplateResponse — templates don't exist on disk (Next.js frontend)
    # Same signature as Starlette's: request first. Starlette 1.0 dropped the
    # old (name, {"request": ...}) form, so a fake that accepts it hides a crash.
    def _fake_template_response(request, name, context=None, **kwargs):
        if not isinstance(name, str):
            raise TypeError("TemplateResponse(request, name, context): name must be a str")
        return HTMLResponse(content=f"<html><body>template:{name}</body></html>")

    if fake_user is _AUTH_DISABLED:
        _user_patch = patch("database.get_current_user", new_callable=AsyncMock,
                            return_value=FakeUser())
    elif fake_user is None:
        from contextlib import nullcontext
        _user_patch = nullcontext()
    else:
        _user_patch = patch("database.get_current_user", new_callable=AsyncMock,
                            return_value=fake_user)

    # Patch templates FIRST — before any router imports bind the real Jinja2Templates object
    with patch("templating.templates") as mock_templates, \
         patch("main.start_scheduler", new_callable=AsyncMock), \
         patch("main.stop_scheduler"), \
         patch("main.init_db", new_callable=AsyncMock), \
         patch("models.init_db", new_callable=AsyncMock), \
         patch("services.syslog.start_syslog_server", new_callable=AsyncMock), \
         patch("services.syslog.stop_syslog_server", new_callable=AsyncMock), \
         patch("database.AsyncSessionLocal", side_effect=fake_session), \
         patch("main.AsyncSessionLocal", side_effect=fake_session), \
         patch("models.base.AsyncSessionLocal", side_effect=fake_session), \
         patch("routers.agents.AsyncSessionLocal", side_effect=fake_session), \
         _user_patch, \
         patch("services.clickhouse_client.query", side_effect=_ch_query_mock), \
         patch("services.clickhouse_client.query_scalar", side_effect=_ch_scalar_mock), \
         patch("services.clickhouse_client.get_client", new_callable=AsyncMock), \
         patch("services.clickhouse_client.insert_batch", new_callable=AsyncMock), \
         patch("services.clickhouse_client.insert_ping_checks", new_callable=AsyncMock), \
         patch("services.clickhouse_client.insert_agent_metrics", new_callable=AsyncMock), \
         patch("services.clickhouse_client.insert_bandwidth_metrics", new_callable=AsyncMock):

        mock_templates.TemplateResponse = _fake_template_response

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Generate a valid CSRF token for tests
            import hashlib as _hl, hmac as _hm, secrets as _sec
            _sk = os.environ.get("SECRET_KEY", "test-secret-key-for-pytest")
            _raw = _sec.token_hex(16)
            _sig = _hm.new(_sk.encode(), _raw.encode(), _hl.sha256).hexdigest()[:16]
            _csrf = f"{_raw}.{_sig}"
            ac.cookies.set("ng_csrf", _csrf)
            ac.headers["x-csrf-token"] = _csrf
            yield ac, session_factory

    await engine.dispose()


@pytest.fixture
async def client():
    """Provide an httpx AsyncClient wired to the FastAPI app with auth bypassed."""
    async with make_client() as (ac, _sf):
        yield ac


@pytest.fixture
async def auth_client():
    """AsyncClient with REAL authentication: no session, no API key.

    Yields ``(client, session_factory)`` so a test can seed users/keys.
    The CSRF cookie+header are valid, so a 403 here is never a CSRF artefact
    hiding a missing auth check.
    """
    async with make_client(fake_user=None) as pair:
        yield pair
