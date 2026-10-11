"""Support access (owner decision 8): operators see a tenant's data only with
the tenant's explicit, time-limited approval.

A tenant admin grants support access (``support_grants``: role, reason,
duration). While a grant is active (not expired, not revoked, tenant
active), every installation operator (``users.is_operator``) and every
member of the tenant's provider (``provider_memberships``) without an own
membership of the tenant has access with the grant's role — ``TenantAccess(
via="support")``, resolved by :class:`~nodeglow_ee.tenancy.backend.MultiTenancy`.
An explicit membership always wins. Without an active grant: no access, the
tenant is answered 404 like any foreign tenant.

Audit (in the tenant's own log, so its admins see it): ``support.grant``,
``support.revoke``, ``support.expired`` (by the expiry job), and
``support.access`` for the requests made through a grant — one row per
user, grant, method and path within :data:`ACCESS_AUDIT_WINDOW` seconds
(a dashboard polling every few seconds would otherwise write a row each
time); every write additionally carries ``acting_via = support`` in its own
audit entry. Grants cannot be made or extended through support access.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import exists, select

from models.audit import AuditLog
from models.tenant import ProviderMembership, SupportGrant, Tenant
from services import tenancy
from services.tenancy import ACTING_VIA_SUPPORT, system_scope, tenant_scope

log = logging.getLogger("nodeglow.ee.support")

ROLES = ("readonly", "editor", "admin")
MIN_HOURS = 1
MAX_HOURS = 24 * 14
MAX_REASON = 500
ACCESS_AUDIT_WINDOW = 60.0
_ACCESS_AUDIT_MAX = 10_000

# (user id, grant id, method, path) -> monotonic time of the last audit row
_audited: dict[tuple, float] = {}


def is_active(grant: SupportGrant, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return grant.revoked_at is None and grant.expires_at > now


def grant_status(grant: SupportGrant, now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    if grant.revoked_at is not None:
        return "revoked"
    return "active" if grant.expires_at > now else "expired"


def public(grant: SupportGrant, now: datetime | None = None) -> dict:
    return {
        "id": grant.id,
        "role": grant.role,
        "reason": grant.reason,
        "created_at": _iso(grant.created_at),
        "expires_at": _iso(grant.expires_at),
        "granted_by": grant.granted_by_name,
        "revoked_at": _iso(grant.revoked_at),
        "revoked_by": grant.revoked_by_name,
        "status": grant_status(grant, now),
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


async def support_access_for(db, user) -> dict[int, tuple[int, str]]:
    """``{tenant_id: (grant_id, role)}`` of the active grants this user may
    use: operators get every active tenant's grants, provider members those
    of their providers' tenants. The newest grant wins per tenant."""
    if user is None or getattr(user, "id", None) is None:
        return {}
    now = datetime.utcnow()
    is_operator = bool(getattr(user, "is_operator", False))
    with system_scope("support access: active grants for a user"):
        stmt = (
            select(SupportGrant.id, SupportGrant.tenant_id, SupportGrant.role)
            .join(Tenant, Tenant.id == SupportGrant.tenant_id)
            .where(SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > now,
                   Tenant.status == "active")
            .order_by(SupportGrant.id)
        )
        if not is_operator:
            stmt = stmt.where(exists().where(
                ProviderMembership.user_id == user.id,
                ProviderMembership.provider_id == Tenant.provider_id,
            ))
        rows = (await db.execute(stmt)).all()
    out: dict[int, tuple[int, str]] = {}
    for gid, tid, role in rows:
        out[int(tid)] = (int(gid), role if role in ROLES else "readonly")
    return out


def _request_target(request) -> tuple[str, str]:
    scope = getattr(request, "scope", None) or {}
    if scope.get("type") == "websocket":
        return "WS", str(scope.get("path", ""))
    method = getattr(request, "method", None) or scope.get("method") or "?"
    path = scope.get("path") or getattr(getattr(request, "url", None), "path", "") or ""
    return str(method), str(path)


async def audit_access(request, user, tenant_id: int, grant_id: int, role: str) -> None:
    """``support.access`` in the tenant's log, at most one row per user,
    grant, method and path per :data:`ACCESS_AUDIT_WINDOW`. Never raises."""
    method, path = _request_target(request)
    key = (getattr(user, "id", None), grant_id, method, path)
    now = time.monotonic()
    last = _audited.get(key)
    if last is not None and now - last < ACCESS_AUDIT_WINDOW:
        return
    if len(_audited) > _ACCESS_AUDIT_MAX:
        cutoff = now - ACCESS_AUDIT_WINDOW
        for k in [k for k, t in _audited.items() if t < cutoff]:
            _audited.pop(k, None)
    _audited[key] = now
    try:
        import json

        from models import base as _base
        from services.audit import provider_of

        client = getattr(request, "client", None)
        with tenant_scope(tenant_id):
            async with _base.AsyncSessionLocal() as db:
                db.add(AuditLog(
                    timestamp=datetime.utcnow(), tenant_id=tenant_id,
                    provider_id=await provider_of(db, tenant_id),
                    acting_via=ACTING_VIA_SUPPORT, user_id=getattr(user, "id", None),
                    username=getattr(user, "username", None), action="support.access",
                    target_type="support_grant", target_id=grant_id,
                    details=json.dumps({"method": method, "path": path, "role": role}),
                    ip_address=getattr(client, "host", None),
                ))
                await db.commit()
    except Exception:  # noqa: BLE001 — the audit must not break the request it records
        _audited.pop(key, None)
        log.warning("support access audit failed (tenant %s, grant %s)", tenant_id, grant_id,
                    exc_info=True)


def reset_audit_window() -> None:
    """Test helper."""
    _audited.clear()


def validate_grant(body: dict) -> tuple[str, str, int] | str:
    """``(role, reason, hours)`` from a request body, or an error message."""
    role = body.get("role") or "readonly"
    if role not in ROLES:
        return f"role must be one of {', '.join(ROLES)}"
    reason = body.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        return "Give a reason (at least 3 characters)."
    reason = reason.strip()
    if len(reason) > MAX_REASON:
        return f"The reason is limited to {MAX_REASON} characters."
    try:
        hours = int(body.get("hours", 24))
    except (TypeError, ValueError):
        return "hours must be a whole number"
    if not MIN_HOURS <= hours <= MAX_HOURS:
        return f"Support access lasts {MIN_HOURS} to {MAX_HOURS} hours."
    return role, reason, hours


async def expire_grants(now: datetime | None = None) -> int:
    """Audit the end of every grant that has run out (``support.expired``,
    in its tenant) once. Access already ended at ``expires_at`` — resolution
    checks it on every request; this records it. Returns the number."""
    import json

    from models import base as _base
    from services.audit import provider_of

    now = now or datetime.utcnow()
    done = 0
    async with _base.AsyncSessionLocal() as db:
        with system_scope("support access: expiry"):
            rows = (await db.execute(
                select(SupportGrant).where(
                    SupportGrant.revoked_at.is_(None), SupportGrant.expires_at <= now,
                    SupportGrant.ended_audited_at.is_(None),
                )
            )).scalars().all()
            for grant in rows:
                grant.ended_audited_at = now
                db.add(AuditLog(
                    timestamp=now, tenant_id=grant.tenant_id,
                    provider_id=await provider_of(db, grant.tenant_id),
                    acting_via=tenancy.ACTING_VIA_SYSTEM, action="support.expired",
                    target_type="support_grant", target_id=grant.id,
                    target_name=grant.reason[:256],
                    details=json.dumps({"expires_at": _iso(grant.expires_at), "role": grant.role}),
                ))
                done += 1
            if done:
                await db.commit()
    return done


def expiry_for(hours: int, now: datetime | None = None) -> datetime:
    return (now or datetime.utcnow()) + timedelta(hours=hours)
