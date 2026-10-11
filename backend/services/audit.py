"""Audit log service — record user actions.

Every entry carries (spec "Audit"):

* ``tenant_id`` — from the tenant context (stamped by the session filter);
  NULL for installation- or provider-level actions, which are written in
  ``system_scope``.
* ``provider_id`` — the provider of that tenant.
* ``acting_via`` — how the actor had access: ``membership`` (or
  ``provider_role`` / ``operator`` from an ee backend), ``api_key``,
  ``agent`` (an agent or install token), ``system`` (no request).

Writes that happen in ``system_scope`` are audited by the session filter
itself (``services.tenancy``), not here.
"""
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from models.audit import AuditLog
from services import tenancy

# tenant id -> provider id. Tenants never change provider in the core (an ee
# move would have to clear this).
_provider_of: dict[int, int] = {}


def _actor(request) -> tuple[int | None, str | None]:
    """Who performed the request: the session user, else the API key.

    An API-key request has no session user (the middleware ignores the cookie
    when X-API-Key is present), so without the key fallback every action taken
    through the REST API was logged with no actor at all.
    """
    if request is None:
        return None, None
    state = getattr(request, "state", None)
    user = getattr(state, "current_user", None) if state is not None else None
    if user is not None:
        return getattr(user, "id", None), getattr(user, "username", None)
    key = getattr(state, "api_key", None) if state is not None else None
    if key is not None and getattr(key, "id", 0):
        # Prefix first: it identifies the key even if a long name is cut.
        prefix = getattr(key, "prefix", None) or "?"
        return None, f"apikey:{prefix}:{key.name}"[:64]
    return None, None


def acting_via(request) -> str:
    """How the request's actor had access to the tenant."""
    if request is None:
        return tenancy.ACTING_VIA_SYSTEM
    state = getattr(request, "state", None)
    via = getattr(state, "acting_via", None) if state is not None else None
    if getattr(state, "current_user", None) is not None:
        return via or tenancy.ACTING_VIA_MEMBERSHIP
    key = getattr(state, "api_key", None) if state is not None else None
    if key is not None and getattr(key, "id", 0):
        return tenancy.ACTING_VIA_API_KEY
    if via:
        return via
    return tenancy.ACTING_VIA_AGENT


async def provider_of(db: AsyncSession, tenant_id: int | None) -> int | None:
    if tenant_id is None:
        return None
    hit = _provider_of.get(tenant_id)
    if hit is not None:
        return hit
    from models.tenant import Tenant

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return None
    _provider_of[tenant_id] = tenant.provider_id
    return tenant.provider_id


def _context_tenant(db: AsyncSession) -> int | None:
    """The tenant the entry belongs to; None in system scope (installation
    level). Raises without any context, like every tenant-owned write."""
    if tenancy.in_system_scope():
        return None
    return tenancy.session_tenant_id(db)


async def log_action(
    db: AsyncSession,
    request,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
    details: dict | None = None,
):
    user_id, username = _actor(request)
    ip = request.client.host if request and request.client else None
    tenant_id = _context_tenant(db)
    db.add(AuditLog(
        timestamp=datetime.utcnow(),
        tenant_id=tenant_id,
        provider_id=await provider_of(db, tenant_id),
        acting_via=acting_via(request),
        user_id=user_id,
        username=username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        details=json.dumps(details) if details else None,
        ip_address=ip,
    ))
