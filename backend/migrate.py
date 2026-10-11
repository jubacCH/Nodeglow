"""Bring the database schema up to date before the application starts.

Two situations have to be handled, and conflating them is exactly what caused
the production schema drift (code for revision 030 running against schema 028):

**Fresh install** — no ``alembic_version`` table. The migration chain cannot
build the schema from nothing: it grew alongside a codebase where tables were
created by SQLAlchemy's ``create_all()``, so several tables (``agents`` among
them) are defined only in the models and in no migration. Running
``alembic upgrade head`` here fails with "relation ... does not exist".
The correct action is to create the schema from the models and stamp it as
current.

**Existing install** — ``alembic_version`` is present. Run the outstanding
migrations and nothing else.

Both run on the **owner** connection (``DATABASE_OWNER_URL``, falling back to
``DATABASE_URL``). Afterwards the row-level-security setup
(``services/pg_rls.py``: app role, grants, policies, ``NODEGLOW_RLS``) is
brought in line — on every run, because a fresh install never executes the
revision that introduced it.

Run as ``python migrate.py``; exits non-zero if the schema could not be brought
up to date, so the caller can refuse to start the app on a drifted schema.
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[migrate] %(message)s")
log = logging.getLogger("nodeglow.migrate")
# alembic.ini's logging config sets the root logger to WARNING when alembic
# runs in-process; these messages are what an operator reads after an update.
for _name in ("nodeglow.migrate", "nodeglow.rls"):
    logging.getLogger(_name).setLevel(logging.INFO)

ALEMBIC_CONFIG = "alembic.ini"


async def has_alembic_version(engine) -> bool:
    """True when this database has already been placed under Alembic control."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT to_regclass('public.alembic_version')")
        )
        return result.scalar() is not None


async def create_schema_from_models(engine) -> None:
    """Create every table the models define (fresh installs only)."""
    from models import init_db

    await init_db(bind=engine)


def stamp_head() -> None:
    """Mark the freshly created schema as being at the latest revision."""
    from alembic import command
    from alembic.config import Config

    command.stamp(Config(ALEMBIC_CONFIG), "head")


def upgrade_head() -> None:
    """Apply outstanding migrations to an existing database."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(ALEMBIC_CONFIG), "head")


async def _probe_and_prepare() -> bool:
    """Do all async work in one loop; return True if the database already exists.

    Runs on the owner connection (``DATABASE_OWNER_URL``, default
    ``DATABASE_URL``): the tables created here belong to the owner, never
    to the app role. The engine binds to the loop it is first used in, so
    probing and schema creation share a single ``asyncio.run`` and dispose
    before returning.
    """
    from models.base import create_owner_engine

    engine = create_owner_engine()
    try:
        existing = await has_alembic_version(engine)
        if not existing:
            log.info("fresh database — creating schema from models")
            await create_schema_from_models(engine)
        return existing
    finally:
        await engine.dispose()


async def _setup_row_level_security() -> None:
    """Roles, grants, policies and the RLS switch (services.pg_rls).

    Every start, after the schema is at head: a fresh install never runs
    revision 044, a table added later needs its policy and grants, and
    ``NODEGLOW_RLS`` may have been switched. Idempotent; DDL only where the
    state differs. A failure is fatal only when RLS is wanted — with
    NODEGLOW_RLS off the app does not depend on any of it.
    """
    import config
    from models.base import create_owner_engine
    from services import pg_rls
    from services.tenancy import tenant_tables

    engine = create_owner_engine()
    try:
        if engine.dialect.name != "postgresql":
            return
        tables = sorted(t.name for t in tenant_tables())
        login = pg_rls.app_login()
        try:
            async with engine.begin() as conn:
                report = await conn.run_sync(
                    lambda c: pg_rls.apply(c, tables, enabled=config.RLS_ENABLED, login=login)
                )
        except Exception as exc:
            if config.RLS_ENABLED:
                raise
            log.warning("row-level security setup skipped (NODEGLOW_RLS is off): %s", exc)
            return
        state = "enabled and forced" if report.rls_enabled else "prepared, switched off (NODEGLOW_RLS=off)"
        log.info("row-level security %s on %d tenant tables; app role %s%s",
                 state, len(report.tables),
                 pg_rls.APP_ROLE if report.app_role else "missing",
                 f", app login {report.app_login}" if report.app_login else "")
    finally:
        await engine.dispose()


def main() -> int:
    """Bring the schema up to date.

    Alembic's ``env.py`` drives its own event loop, so its commands run only
    after the async phase above has finished and closed its loop — nesting the
    two makes alembic's coroutine never awaited.
    """
    try:
        existing = _probe()

        if existing:
            log.info("existing database — applying outstanding migrations")
            upgrade_head()
            log.info("schema is at head")
        else:
            stamp_head()
            log.info("schema created and stamped at head")
        asyncio.run(_setup_row_level_security())
    except Exception as exc:  # noqa: BLE001
        log.error("failed to bring schema up to date: %s", exc)
        return 1
    return 0


def _probe() -> bool:
    return asyncio.run(_probe_and_prepare())


if __name__ == "__main__":
    sys.exit(main())
