"""HTTP routes of the multi-tenancy package.

Installation operator (``require_operator``; operator-only views of the
whole installation — no tenant data beyond names, plans and member counts):

* ``GET    /api/tenants`` — tenants, licence usage (x / max), plans, what
  stands between the installation and one more tenant;
* ``POST   /api/tenants`` — create (with its first admin);
* ``PATCH  /api/tenants/{tenant_id}`` — rename, assign a plan;
* ``POST   /api/tenants/{tenant_id}/suspend`` | ``/resume`` |
  ``/delete`` (``{"confirm": "<tenant name>"}``) | ``/cancel-deletion``.

The tenant's own (request tenant, filtered like every tenant route):

* ``GET    /api/support-access`` — active grants (every member: the banner);
  the history and the right to manage for admins;
* ``POST   /api/support-access`` — grant (admin, not through support access);
* ``DELETE /api/support-access/{grant_id}`` — revoke (admin).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.tenant import SupportGrant, Tenant
from ratelimit import rate_limit
from routers.settings._helpers import require_admin, require_operator
from services.audit import log_action
from services.tenancy import ACTING_VIA_SUPPORT

from nodeglow_ee import license_runtime
from nodeglow_ee.tenancy import FEATURE, lifecycle, plans, support

router = APIRouter(tags=["Tenants"])


def _error(exc: lifecycle.LifecycleError) -> JSONResponse:
    body = {"error": exc.message}
    if exc.code:
        body["code"] = exc.code
    return JSONResponse(body, status_code=exc.status)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ── Installation operator: tenants ───────────────────────────────────────────

@router.get("/api/tenants", summary="Tenants of the installation (operator)")
async def list_tenants(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_operator(request):
        return err
    tenants = (await db.execute(select(Tenant).order_by(Tenant.id))).scalars().all()
    members = await lifecycle.member_counts(db)
    return {
        "tenants": [lifecycle.public(t, members=members.get(t.id, 0)) for t in tenants],
        "usage": await lifecycle.usage(db),
        "plans": plans.plan_list(),
        "default_plan": plans.DEFAULT_PLAN,
        "creation_problems": await lifecycle.creation_problems(db),
        "deletion_grace_days": lifecycle.DELETION_GRACE_DAYS,
    }


@router.post("/api/tenants", summary="Create a tenant (operator)")
@rate_limit(max_requests=10, window_seconds=60)
async def create_tenant(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_operator(request):
        return err
    try:
        tenant = await lifecycle.create_tenant(db, request, await _body(request))
    except lifecycle.LifecycleError as exc:
        await db.rollback()
        return _error(exc)
    return JSONResponse(lifecycle.public(tenant, members=1), status_code=201)


async def _tenant_action(request: Request, db: AsyncSession, tenant_id: int, action):
    if err := require_operator(request):
        return err
    try:
        tenant = await lifecycle.get_tenant(db, tenant_id)
        tenant = await action(tenant)
    except lifecycle.LifecycleError as exc:
        await db.rollback()
        return _error(exc)
    members = (await lifecycle.member_counts(db)).get(tenant.id, 0)
    return lifecycle.public(tenant, members=members)


@router.patch("/api/tenants/{tenant_id}", summary="Rename a tenant or assign its plan (operator)")
async def update_tenant(tenant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    body = await _body(request)
    return await _tenant_action(request, db, tenant_id,
                                lambda t: lifecycle.update_tenant(db, request, t, body))


@router.post("/api/tenants/{tenant_id}/suspend", summary="Suspend a tenant (operator)")
async def suspend_tenant(tenant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    return await _tenant_action(request, db, tenant_id,
                                lambda t: lifecycle.suspend(db, request, t))


@router.post("/api/tenants/{tenant_id}/resume", summary="Resume a suspended tenant (operator)")
async def resume_tenant(tenant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    return await _tenant_action(request, db, tenant_id,
                                lambda t: lifecycle.resume(db, request, t))


@router.post("/api/tenants/{tenant_id}/delete",
             summary="Schedule a tenant's deletion: suspended now, purged after 30 days (operator)")
async def delete_tenant(tenant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    body = await _body(request)
    return await _tenant_action(request, db, tenant_id,
                                lambda t: lifecycle.schedule_deletion(db, request, t,
                                                                      body.get("confirm")))


@router.post("/api/tenants/{tenant_id}/cancel-deletion",
             summary="Cancel a scheduled deletion; the tenant stays suspended (operator)")
async def cancel_tenant_deletion(tenant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    return await _tenant_action(request, db, tenant_id,
                                lambda t: lifecycle.cancel_deletion(db, request, t))


# ── The tenant's own: support access ─────────────────────────────────────────

def _via_support(request: Request) -> bool:
    return getattr(request.state, "acting_via", None) == ACTING_VIA_SUPPORT


@router.get("/api/support-access", summary="Support access of this tenant")
async def get_support_access(request: Request, db: AsyncSession = Depends(get_db)):
    from services.tenancy import request_is_admin

    now = datetime.utcnow()
    grants = (await db.execute(
        select(SupportGrant).order_by(SupportGrant.id.desc()).limit(50)
    )).scalars().all()
    is_admin = request_is_admin(request)
    licensed = await license_runtime.is_active(FEATURE)
    return {
        "active": [support.public(g, now) for g in grants if support.is_active(g, now)],
        "history": [support.public(g, now) for g in grants] if is_admin else [],
        "can_manage": is_admin and not _via_support(request),
        "can_grant": is_admin and not _via_support(request) and licensed,
        "via_support": _via_support(request),
        "limits": {"min_hours": support.MIN_HOURS, "max_hours": support.MAX_HOURS,
                   "roles": list(support.ROLES)},
    }


@router.post("/api/support-access", summary="Grant time-limited support access (admin)")
@rate_limit(max_requests=10, window_seconds=60)
async def grant_support_access(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    if _via_support(request):
        return JSONResponse({"error": "Support access cannot grant or extend support access."},
                            status_code=403)
    if refused := await license_runtime.blocked(FEATURE):
        return refused
    parsed = support.validate_grant(await _body(request))
    if isinstance(parsed, str):
        return JSONResponse({"error": parsed}, status_code=400)
    role, reason, hours = parsed
    user = getattr(request.state, "current_user", None)
    now = datetime.utcnow()
    grant = SupportGrant(role=role, reason=reason, created_at=now,
                         expires_at=support.expiry_for(hours, now),
                         granted_by=getattr(user, "id", None),
                         granted_by_name=getattr(user, "username", None))
    db.add(grant)
    await db.flush()
    await log_action(db, request, "support.grant", "support_grant", grant.id, reason[:256],
                     details={"role": role, "hours": hours,
                              "expires_at": support.public(grant, now)["expires_at"]})
    await db.commit()
    return JSONResponse(support.public(grant, now), status_code=201)


@router.delete("/api/support-access/{grant_id}", summary="Revoke support access (admin)")
async def revoke_support_access(grant_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    # Ending access early is always allowed (also without a licence, also by
    # the support user itself).
    if err := require_admin(request):
        return err
    grant = await db.get(SupportGrant, grant_id)
    if grant is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    now = datetime.utcnow()
    if grant.revoked_at is None:
        user = getattr(request.state, "current_user", None)
        was_active = support.is_active(grant, now)
        grant.revoked_at = now
        grant.revoked_by = getattr(user, "id", None)
        grant.revoked_by_name = getattr(user, "username", None)
        if was_active:
            grant.ended_audited_at = now
        await log_action(db, request, "support.revoke", "support_grant", grant.id,
                         grant.reason[:256], details={"was_active": was_active})
        await db.commit()
    return support.public(grant, now)
