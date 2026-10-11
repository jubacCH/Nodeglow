"""Tenant lifecycle (enterprise): create, suspend, resume, schedule deletion.

States (``tenants.status``)::

    active ──suspend──▶ suspended ──resume──▶ active
      │                    │
      └──delete────────────┴──▶ deleting ──(30 days)──▶ purged (row gone)
                                   │
                                   └──cancel deletion──▶ suspended

* A suspended or deleting tenant is not monitored (the per-tenant jobs skip
  it), nobody can sign in to it, its API keys, agent and install tokens stop
  working (core: ``tenancy.tenant_is_active``). Nothing is deleted.
* Deletion (owner decision 6) keeps the tenant suspended for
  :data:`DELETION_GRACE_DAYS` days, then the scheduled job purges it
  (:mod:`.purge`). The request needs the tenant's name typed as
  confirmation; the answer names the purge date.
* The default tenant (id 1, the installation's own) cannot be suspended or
  deleted.

Licence: creating a tenant needs the ``multi_tenancy`` feature and a free
slot under ``max_tenants`` (``None`` in the licence = no limit). The second
tenant additionally needs the ClickHouse rebuild (step B) completed and
PostgreSQL row-level security active — or, for an install that knowingly
runs without RLS, ``NODEGLOW_TENANTS_WITHOUT_RLS=1`` (documented in the
spec; the application filter is then the only layer). The other admin
actions need the feature too, except cancelling a deletion, which only ever
prevents data loss. On expiry nothing changes for existing tenants.

Every action is audited twice: at installation level (the operator's
record, ``tenant_id`` NULL, ``acting_via = operator``) and in the tenant's
own log (its admins see who suspended or scheduled it).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from models.settings import Session, User
from models.tenant import DEFAULT_PROVIDER_ID, DEFAULT_TENANT_ID, Provider, Site, Tenant, TenantMembership
from services import memberships as ms
from services.audit import log_action
from services.tenancy import ACTING_VIA_OPERATOR, system_scope, tenant_scope

from nodeglow_ee import license_runtime
from nodeglow_ee.tenancy import FEATURE, plans

log = logging.getLogger("nodeglow.ee.tenancy.lifecycle")

DELETION_GRACE_DAYS = 30
RLS_OVERRIDE_ENV = "NODEGLOW_TENANTS_WITHOUT_RLS"
_TRUE = ("1", "true", "yes", "on")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class LifecycleError(Exception):
    """A refused lifecycle action: the message is for the operator."""

    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ── Preconditions ────────────────────────────────────────────────────────────

async def licence_status():
    return await license_runtime.manager.current()


async def licensed() -> bool:
    """The licence gate of every lifecycle admin action (``multi_tenancy``)."""
    return await license_runtime.is_active(FEATURE)


async def require_licence() -> None:
    if await licensed():
        return
    status = await licence_status()
    raise LifecycleError(license_runtime.block_message(status, FEATURE), 402,
                         license_runtime._block_code(status, FEATURE))


async def tenant_count(db) -> int:
    """Tenants that exist (active, suspended, deleting) — what max_tenants counts."""
    return int((await db.execute(select(func.count()).select_from(Tenant))).scalar() or 0)


def rls_override() -> bool:
    return os.environ.get(RLS_OVERRIDE_ENV, "").strip().lower() in _TRUE


async def rls_problem(db) -> str | None:
    """Why PostgreSQL row-level security does not protect this installation,
    or None (also None with the documented override)."""
    if rls_override():
        return None
    import logging as _logging

    import config
    from services import pg_rls
    from services.tenancy import tenant_tables

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return (f"Row-level security needs PostgreSQL (this database is {bind.dialect.name}). "
                f"Set {RLS_OVERRIDE_ENV}=1 to run several tenants with the application filter only.")
    if not config.RLS_ENABLED:
        return ("PostgreSQL row-level security is off (NODEGLOW_RLS). Switch it on first "
                "(docs/OPERATIONS.md, 'Row-level security'), or set "
                f"{RLS_OVERRIDE_ENV}=1 to accept the application filter as the only layer.")
    tables = sorted(t.name for t in tenant_tables())
    conn = await db.connection()
    status = await conn.run_sync(lambda c: pg_rls.runtime_status(c, tables))
    serious = [msg for level, msg in pg_rls.assess(status, True) if level >= _logging.WARNING]
    if serious:
        return "PostgreSQL row-level security is not active: " + " ".join(serious)
    return None


async def clickhouse_problem() -> str | None:
    """None when every ClickHouse tenant table sorts by tenant (step B)."""
    from services import clickhouse_rebuild

    try:
        done = await clickhouse_rebuild.complete()
    except Exception as exc:  # noqa: BLE001 — unknown state refuses, it does not crash
        return f"The ClickHouse tenant rebuild cannot be checked ({exc})."
    if not done:
        return ("The ClickHouse tenant rebuild (step B) has not completed yet. It runs hourly; "
                "its state is in System → Status (clickhouse_tenant_rebuild).")
    return None


async def creation_problems(db) -> list[str]:
    """Everything that stands between this installation and one more tenant."""
    problems: list[str] = []
    status = await licence_status()
    if not await licensed():
        problems.append(license_runtime.block_message(status, FEATURE))
    count = await tenant_count(db)
    limit = status.license.max_tenants if status.license is not None else None
    if limit is not None and count >= limit:
        problems.append(f"The licence allows {limit} tenant{'s' if limit != 1 else ''} "
                        f"and {count} exist{'s' if count == 1 else ''}.")
    if count >= 1:  # the next one is the second (or later)
        for check in (await clickhouse_problem(), await rls_problem(db)):
            if check:
                problems.append(check)
    return problems


async def usage(db) -> dict:
    status = await licence_status()
    lic = status.license
    return {
        "count": await tenant_count(db),
        "max": lic.max_tenants if lic is not None else None,
        "licence": status.public(),
        "licensed": await licensed(),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def public(tenant: Tenant, *, members: int | None = None) -> dict:
    plan = plans.get_plan(tenant.plan)
    return {
        "id": tenant.id, "name": tenant.name, "slug": tenant.slug,
        "provider_id": tenant.provider_id, "status": tenant.status,
        "plan": tenant.plan, "plan_name": plan.name if plan else None,
        "retention_days": plan.retention_days if plan else None,
        "created_at": _iso(tenant.created_at), "suspended_at": _iso(tenant.suspended_at),
        "delete_after": _iso(tenant.delete_after),
        "deletion_requested_at": _iso(tenant.deletion_requested_at),
        "purge_started_at": _iso(tenant.purge_started_at),
        "members": members, "default": tenant.id == DEFAULT_TENANT_ID,
    }


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "tenant"


async def _audit(db, request, action: str, tenant: Tenant, details: dict | None = None,
                 *, in_tenant: bool = True) -> None:
    """The operator's record (installation level) and the tenant's own."""
    if request is not None and getattr(request, "state", None) is not None:
        request.state.acting_via = ACTING_VIA_OPERATOR
    # Each row is flushed inside its scope: flushed later (at the commit, in
    # the operator's own tenant) the session filter would stamp the
    # installation row with that tenant and refuse the other tenant's row.
    with system_scope(f"tenant lifecycle: {action}"):
        await log_action(db, request, action, "tenant", tenant.id, tenant.name, details)
        await db.flush()
    if in_tenant:
        with tenant_scope(tenant.id):
            await log_action(db, request, action, "tenant", tenant.id, tenant.name, details)
            await db.flush()


async def _clear_sessions(db, tenant_id: int) -> None:
    """Sessions working in the tenant fall back to the user's other access."""
    await db.execute(update(Session).where(Session.active_tenant_id == tenant_id)
                     .values(active_tenant_id=None))


def _refuse_default(tenant: Tenant, what: str) -> None:
    if tenant.id == DEFAULT_TENANT_ID:
        raise LifecycleError(f"The default tenant cannot be {what}.", 409, "default_tenant")


async def get_tenant(db, tenant_id: int) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise LifecycleError("Not found", 404)
    return tenant


async def member_counts(db) -> dict[int, int]:
    rows = (await db.execute(
        select(TenantMembership.tenant_id, func.count()).group_by(TenantMembership.tenant_id)
    )).all()
    return {int(t): int(n) for t, n in rows}


# ── Actions ──────────────────────────────────────────────────────────────────

async def create_tenant(db, request, body: dict) -> Tenant:
    """Create a tenant with its default site and its first admin.

    ``body``: ``name`` (required), ``slug`` (optional, from the name),
    ``plan`` (default :data:`plans.DEFAULT_PLAN`), ``provider_id``
    (default 1), ``admin_username`` (required: an existing user becomes the
    tenant's admin, a new one is created with ``admin_password``). The
    operator gets no membership — operators see tenant data only through
    support access (owner decision 8).
    """
    from utils.password import hash_password, validate_password

    name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
    if not name or len(name) > 128:
        raise LifecycleError("Give the tenant a name (up to 128 characters).")
    slug = body.get("slug")
    slug = slugify(name) if not isinstance(slug, str) or not slug.strip() else slug.strip().lower()
    if not _SLUG.match(slug):
        raise LifecycleError("The short name may contain a-z, 0-9 and '-' (up to 64 characters).")
    plan_key = body.get("plan") or plans.DEFAULT_PLAN
    plan = plans.get_plan(plan_key)
    if plan is None:
        raise LifecycleError(f"Unknown plan {plan_key!r}.")
    try:
        provider_id = int(body.get("provider_id") or DEFAULT_PROVIDER_ID)
    except (TypeError, ValueError):
        raise LifecycleError("provider_id must be a number.") from None
    if await db.get(Provider, provider_id) is None:
        raise LifecycleError("Unknown provider.", 404)
    admin_username = body.get("admin_username")
    admin_username = admin_username.strip() if isinstance(admin_username, str) else ""
    if not admin_username or len(admin_username) > 64:
        raise LifecycleError("Name the tenant's first admin (a new or an existing user).")

    problems = await creation_problems(db)
    if problems:
        raise LifecycleError(" ".join(problems), 409 if await licensed() else 402,
                             "tenant_creation_refused")
    if (await db.execute(select(Tenant.id).where(Tenant.provider_id == provider_id,
                                                 Tenant.slug == slug))).first():
        raise LifecycleError(f"A tenant with the short name {slug!r} exists already.", 409)

    admin = (await db.execute(select(User).where(User.username == admin_username))).scalar_one_or_none()
    if admin is None:
        password = body.get("admin_password")
        if not isinstance(password, str) or not password:
            raise LifecycleError("Set a password for the new admin user.")
        if err := validate_password(password):
            raise LifecycleError(err)

    operator = getattr(getattr(request, "state", None), "current_user", None)
    now = datetime.utcnow()
    with system_scope("tenant lifecycle: create tenant"):
        tenant = Tenant(provider_id=provider_id, name=name, slug=slug, status="active",
                        plan=plan.key, created_at=now)
        db.add(tenant)
        # The tenant first: the unit of work does not order the inserts
        # below by foreign key (no relationship()), PostgreSQL enforces them.
        await db.flush()
        site = Site(tenant_id=tenant.id, name="Default", created_at=now)
        db.add(site)
        await db.flush()
        if admin is None:
            admin = User(username=admin_username, password_hash=hash_password(body["admin_password"]),
                         role="admin", auth_source="local")
            db.add(admin)
            await db.flush()
        await ms.grant(db, admin, tenant.id, "admin", created_by=getattr(operator, "id", None))

    from nodeglow_ee.tenancy.backend import remember_default_site

    remember_default_site(tenant.id, site.id)
    with tenant_scope(tenant.id):
        from models.settings import set_setting

        await set_setting(db, "site_name", name, commit=False)
    await _audit(db, request, "tenant.create", tenant, {
        "slug": slug, "plan": plan.key, "provider_id": provider_id, "admin": admin.username,
    })
    await db.commit()
    log.info("Tenant %s (%s) created by %s", tenant.id, slug, getattr(operator, "username", None))
    return tenant


async def suspend(db, request, tenant: Tenant) -> Tenant:
    await require_licence()
    _refuse_default(tenant, "suspended")
    if tenant.status != "active":
        raise LifecycleError(f"The tenant is {tenant.status}, not active.", 409)
    tenant.status = "suspended"
    tenant.suspended_at = datetime.utcnow()
    await _clear_sessions(db, tenant.id)
    await _audit(db, request, "tenant.suspend", tenant)
    await db.commit()
    return tenant


async def resume(db, request, tenant: Tenant) -> Tenant:
    await require_licence()
    if tenant.status != "suspended":
        raise LifecycleError(f"The tenant is {tenant.status}; only a suspended tenant can be resumed.",
                             409)
    tenant.status = "active"
    tenant.suspended_at = None
    await _audit(db, request, "tenant.resume", tenant)
    await db.commit()
    return tenant


async def schedule_deletion(db, request, tenant: Tenant, confirm) -> Tenant:
    """Suspend now, purge after :data:`DELETION_GRACE_DAYS` days. ``confirm``
    must be the tenant's name."""
    await require_licence()
    _refuse_default(tenant, "deleted")
    if tenant.status == "deleting":
        raise LifecycleError("The tenant is already scheduled for deletion.", 409)
    if not isinstance(confirm, str) or confirm.strip() != tenant.name:
        raise LifecycleError("Type the tenant's name exactly to confirm the deletion.", 400,
                             "confirmation_mismatch")
    now = datetime.utcnow()
    actor = getattr(getattr(request, "state", None), "current_user", None)
    tenant.status = "deleting"
    tenant.suspended_at = tenant.suspended_at or now
    tenant.delete_after = now + timedelta(days=DELETION_GRACE_DAYS)
    tenant.deletion_requested_at = now
    tenant.deletion_requested_by = getattr(actor, "id", None)
    await _clear_sessions(db, tenant.id)
    await _audit(db, request, "tenant.delete_scheduled", tenant,
                 {"purge_after": _iso(tenant.delete_after)})
    await db.commit()
    return tenant


async def cancel_deletion(db, request, tenant: Tenant) -> Tenant:
    """Back to suspended. Allowed without a licence: it only prevents data
    loss. Refused once the purge has started."""
    if tenant.status != "deleting":
        raise LifecycleError("The tenant is not scheduled for deletion.", 409)
    if tenant.purge_started_at is not None:
        raise LifecycleError("The purge has started; it cannot be cancelled.", 409, "purge_started")
    tenant.status = "suspended"
    tenant.delete_after = None
    tenant.deletion_requested_at = None
    tenant.deletion_requested_by = None
    await _audit(db, request, "tenant.delete_cancelled", tenant)
    await db.commit()
    return tenant


async def update_tenant(db, request, tenant: Tenant, body: dict) -> Tenant:
    """Rename and/or assign a plan (the operator's choice; tenant admins
    choose retention within it)."""
    await require_licence()
    changes: dict = {}
    if "name" in body:
        name = body.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 128:
            raise LifecycleError("Give the tenant a name (up to 128 characters).")
        if name != tenant.name:
            changes["name"] = {"from": tenant.name, "to": name}
            tenant.name = name
            with tenant_scope(tenant.id):
                from models.settings import set_setting

                await set_setting(db, "site_name", name, commit=False)
    if "plan" in body:
        plan = plans.get_plan(body.get("plan"))
        if plan is None:
            raise LifecycleError(f"Unknown plan {body.get('plan')!r}.")
        if plan.key != tenant.plan:
            changes["plan"] = {"from": tenant.plan, "to": plan.key}
            tenant.plan = plan.key
            clamped = await plans.clamp_tenant_retention(db, tenant.id, plan)
            if clamped:
                changes["retention_lowered"] = clamped
    if not changes:
        return tenant
    await _audit(db, request, "tenant.update", tenant, changes)
    await db.commit()
    try:
        from main import invalidate_settings_cache

        invalidate_settings_cache(tenant.id)
    except Exception:  # noqa: BLE001 — the cache expires on its own
        pass
    return tenant


async def due_for_purge(db, now: datetime | None = None) -> list[int]:
    now = now or datetime.utcnow()
    rows = (await db.execute(
        select(Tenant.id).where(Tenant.status == "deleting", Tenant.delete_after.is_not(None),
                                Tenant.delete_after <= now, Tenant.id != DEFAULT_TENANT_ID)
        .order_by(Tenant.delete_after)
    )).scalars().all()
    return [int(t) for t in rows]
