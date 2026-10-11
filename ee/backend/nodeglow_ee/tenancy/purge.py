"""Purge a tenant: every PostgreSQL and ClickHouse row of it, then the tenant.

Runs only for a tenant whose deletion is due (``status = deleting``,
``delete_after`` passed; owner decision 6, nDSG), from the scheduled job
(:mod:`.jobs`) — there is no "purge now" route. Order, each step idempotent
so an interrupted purge resumes from the top on the next run
(``tenants.purge_started_at`` / ``purge_state`` record the progress):

1. **Crypto-shred** — ``services.tenant_keys.destroy_tenant_key(db,
   tenant_id)`` (per-tenant envelope encryption, owner decision 7): every
   wrapped data key of the tenant is deleted, so its secrets become
   unreadable at once in the database and in every dump taken afterwards
   (a dump from before the purge carries the wrapped keys). A failure
   stops the purge before anything is deleted (the next run retries).
2. **ClickHouse** — per tenant table a lightweight ``DELETE … WHERE
   tenant_id = <t>`` (only when a count finds rows), on the main connection
   in the tenant's scope; also the step-B leftovers (``<t>__pre_tenant``)
   while they exist. Rows are invisible at once and gone physically with
   the next merges. ClickHouse first: once the tenant row is gone there is
   nothing left to resume from.
3. **PostgreSQL** — every tenant-owned table, children before parents
   (``Base.metadata.sorted_tables`` reversed; self references cleared
   first), in batches of :data:`BATCH` rows with a commit each, in
   ``system_scope`` (as ``nodeglow_app`` under RLS: ``nodeglow.system =
   on``; plain ``DELETE``, no TRUNCATE, so no owner connection is needed);
   then memberships, the sessions working in it, the users who belong to no
   other tenant (and are neither operators nor provider members), and the
   tenant row.
4. **Audit** — ``tenant.purged`` at installation level with the row counts
   (the tenant's own audit log is part of what is purged).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import delete, func, select, update

from models.base import Base
from models.settings import Session, User
from models.tenant import (
    DEFAULT_TENANT_ID, ProviderMembership, Tenant, TenantMembership,
)
from services.tenancy import system_scope, tenant_scope, tenant_tables

log = logging.getLogger("nodeglow.ee.tenancy.purge")

BATCH = 5000


class PurgeRefused(Exception):
    pass


def _session_factory():
    from models import base as _base

    return _base.AsyncSessionLocal


def purge_order() -> list:
    """Tenant-owned tables, children before parents."""
    owned = set(tenant_tables())
    return [t for t in reversed(Base.metadata.sorted_tables) if t in owned]


def _self_references(table) -> list[str]:
    cols = []
    for fk in table.foreign_key_constraints:
        if fk.referred_table is table:
            cols.extend(c.name for c in fk.columns if c.name != "tenant_id")
    return cols


async def _save_state(tenant_id: int, state: dict) -> None:
    async with _session_factory()() as db:
        await db.execute(update(Tenant).where(Tenant.id == tenant_id)
                         .values(purge_state=json.dumps(state, sort_keys=True)))
        await db.commit()


async def _crypto_shred(db, tenant_id: int) -> str:
    """Destroy the tenant's data keys (owner decision 7). Fails the purge
    when it fails: nothing is deleted while the secrets could still be
    read from a backup taken before the purge."""
    from services import tenant_keys  # core, per-tenant envelope encryption

    destroyed = await tenant_keys.destroy_tenant_key(db, tenant_id)
    await db.commit()
    log.warning("Purge of tenant %s: %d data key(s) destroyed", tenant_id, destroyed)
    return "destroyed"


async def purge_clickhouse(tenant_id: int) -> dict[str, int]:
    """Delete the tenant's rows from every ClickHouse tenant table. Returns
    the rows found (and deleted) per table. Raises when ClickHouse fails —
    the purge then stops before PostgreSQL and is retried."""
    from services import clickhouse_client as ch
    from services import clickhouse_rebuild

    out: dict[str, int] = {}
    client = await ch.get_client()
    with tenant_scope(tenant_id):
        for table in ch.TENANT_TABLES:
            params: dict = {}
            where = ch.tenant_clause(params)
            found = int(await ch.query_scalar(f"SELECT count() FROM {table} WHERE {where}", params) or 0)
            if found:
                await client.command(f"DELETE FROM {table} WHERE {where}", parameters=params)
                left = int(await ch.query_scalar(
                    f"SELECT count() FROM {table} WHERE {ch.TENANT_FILTER}", {}) or 0)
                if left:
                    raise RuntimeError(f"{left} rows of tenant {tenant_id} still in {table}")
            out[table] = found
    # Step-B leftovers (kept for the grace period, or in 'attention').
    with system_scope(f"purge tenant {tenant_id}: ClickHouse rebuild leftovers"):
        for table in ch.TENANT_TABLES:
            old = table + clickhouse_rebuild.OLD_SUFFIX
            try:
                exists = await clickhouse_rebuild.table_info(client, old)
            except Exception:  # noqa: BLE001 — metadata unreadable: treat as absent, logged
                log.warning("purge: could not check %s", old, exc_info=True)
                exists = None
            if exists is None:
                continue
            params = {ch.TENANT_PARAM: tenant_id}
            await client.command(f"DELETE FROM {old} WHERE {ch.TENANT_FILTER}", parameters=params)
            out[old] = -1  # counted in its live table
    return out


async def _delete_batched(table, tenant_id: int) -> int:
    """Delete the tenant's rows of one table in batches; commits each."""
    total = 0
    pk = list(table.primary_key.columns)
    while True:
        async with _session_factory()() as db:
            with system_scope(f"purge tenant {tenant_id}: {table.name}"):
                if len(pk) == 1 and pk[0].name == "id":
                    ids = select(table.c.id).where(table.c.tenant_id == tenant_id).limit(BATCH)
                    result = await db.execute(delete(table).where(table.c.id.in_(ids.scalar_subquery())))
                else:
                    result = await db.execute(delete(table).where(table.c.tenant_id == tenant_id))
                await db.commit()
        n = result.rowcount or 0
        total += n
        if n < BATCH or not (len(pk) == 1 and pk[0].name == "id"):
            return total


async def purge_postgres(tenant_id: int, state: dict) -> dict[str, int]:
    counts: dict[str, int] = dict(state.get("postgres") or {})
    order = purge_order()
    async with _session_factory()() as db:
        with system_scope(f"purge tenant {tenant_id}: clear self references"):
            for table in order:
                for col in _self_references(table):
                    await db.execute(update(table).where(table.c.tenant_id == tenant_id,
                                                         table.c[col].is_not(None))
                                     .values({col: None}))
            await db.commit()
    for table in order:
        counts[table.name] = counts.get(table.name, 0) + await _delete_batched(table, tenant_id)
        state["postgres"] = counts
        await _save_state(tenant_id, state)
    return counts


async def _purge_identities(tenant_id: int) -> dict[str, int]:
    """Memberships, sessions in the tenant, and users of this tenant only."""
    async with _session_factory()() as db:
        with system_scope(f"purge tenant {tenant_id}: memberships and users"):
            members = set((await db.execute(
                select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id)
            )).scalars().all())
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id == tenant_id))
            await db.execute(update(Session).where(Session.active_tenant_id == tenant_id)
                             .values(active_tenant_id=None))
            orphans: list[int] = []
            for uid in sorted(members):
                other = (await db.execute(select(func.count()).select_from(TenantMembership)
                                          .where(TenantMembership.user_id == uid))).scalar() or 0
                msp = (await db.execute(select(func.count()).select_from(ProviderMembership)
                                        .where(ProviderMembership.user_id == uid))).scalar() or 0
                user = await db.get(User, uid)
                if user is not None and not other and not msp and not user.is_operator:
                    orphans.append(uid)
            if orphans:
                await db.execute(delete(Session).where(Session.user_id.in_(orphans)))
                await db.execute(update(TenantMembership).where(TenantMembership.created_by.in_(orphans))
                                 .values(created_by=None))
                await db.execute(delete(User).where(User.id.in_(orphans)))
            await db.commit()
    return {"memberships": len(members), "users_deleted": len(orphans)}


async def purge_tenant(tenant_id: int, *, now: datetime | None = None) -> dict:
    """Purge one due tenant. Returns the report; raises on refusal or failure
    (the job logs it and tries again next time)."""
    now = now or datetime.utcnow()
    if tenant_id == DEFAULT_TENANT_ID:
        raise PurgeRefused("the default tenant is never purged")
    async with _session_factory()() as db:
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise PurgeRefused(f"tenant {tenant_id} does not exist")
        if tenant.status != "deleting" or tenant.delete_after is None or tenant.delete_after > now:
            raise PurgeRefused(f"tenant {tenant_id} is not due for purge")
        name, slug, provider_id = tenant.name, tenant.slug, tenant.provider_id
        try:
            state = json.loads(tenant.purge_state) if tenant.purge_state else {}
        except ValueError:
            state = {}
        if tenant.purge_started_at is None:
            tenant.purge_started_at = now
        state["runs"] = int(state.get("runs", 0)) + 1
        tenant.purge_state = json.dumps(state, sort_keys=True)
        await db.commit()
        log.warning("Purging tenant %s (%s), run %d", tenant_id, slug, state["runs"])
        if state.get("crypto_shred") != "destroyed":
            state["crypto_shred"] = await _crypto_shred(db, tenant_id)
            await _save_state(tenant_id, state)

    state["clickhouse"] = await purge_clickhouse(tenant_id)
    await _save_state(tenant_id, state)
    state["postgres"] = await purge_postgres(tenant_id, state)
    state.update(await _purge_identities(tenant_id))

    async with _session_factory()() as db:
        with system_scope(f"purge tenant {tenant_id}: the tenant row"):
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            from models.audit import AuditLog

            db.add(AuditLog(
                timestamp=datetime.utcnow(), tenant_id=None, provider_id=provider_id,
                acting_via="system", action="tenant.purged", target_type="tenant",
                target_id=tenant_id, target_name=name[:256],
                details=json.dumps({"slug": slug, **state}, sort_keys=True),
            ))
            await db.commit()
    from nodeglow_ee.tenancy.backend import forget_tenant
    from services import audit as _audit

    forget_tenant(tenant_id)
    _audit._provider_of.pop(tenant_id, None)
    log.warning("Tenant %s (%s) purged: %s", tenant_id, slug, state)
    return state
