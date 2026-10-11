"""``MultiTenancy`` — the enterprise tenancy backend.

Replaces the core's ``SingleTenancy`` (registered through
``extensions.registry.set_tenancy_backend``). With one tenant it behaves
exactly like the core; what it adds:

* **every active tenant** is iterated by the per-tenant jobs
  (``active_tenant_ids``), not only tenant 1;
* **support access** (:mod:`.support`): an operator or provider member
  without a membership reaches a tenant while the tenant's admin has granted
  support access, with the grant's role and ``via = support`` — audited;
* **operators** by the ``users.is_operator`` flag (in addition to the core
  rule "admin of the default tenant", which the flag mirrors anyway);
* **login with several tenants** starts in the last used one (the newest
  session's tenant), else the lowest id — the core refuses the login;
* the **default site** of every tenant (new hosts / agents land there).

Resolution order for a request is the core's: ``X-Nodeglow-Tenant`` header
(WebSocket ``?tenant=``), else the session's active tenant, else the only
access; a tenant without access is ``None`` (the middleware answers 404).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from models.tenant import DEFAULT_SITE_ID, DEFAULT_TENANT_ID, Site, Tenant
from services.tenancy import (
    ACTING_VIA_MEMBERSHIP, ACTING_VIA_SUPPORT, SingleTenancy, TenantAccess, _user_memberships,
    requested_tenant, system_scope,
)

from nodeglow_ee.tenancy import support

log = logging.getLogger("nodeglow.ee.tenancy")

# tenant id -> its default site (the oldest). Filled by active_tenant_ids,
# request resolution and tenant creation; read synchronously by the session
# filter when it stamps site_id on a new row.
_default_sites: dict[int, int] = {DEFAULT_TENANT_ID: DEFAULT_SITE_ID}


def remember_default_site(tenant_id: int, site_id: int | None) -> None:
    if site_id is not None:
        _default_sites[int(tenant_id)] = int(site_id)


def reset_state() -> None:
    """Forget the cached default sites and support grants (tests)."""
    _default_sites.clear()
    _default_sites[DEFAULT_TENANT_ID] = DEFAULT_SITE_ID
    _grant_of.clear()


def forget_tenant(tenant_id: int) -> None:
    if tenant_id != DEFAULT_TENANT_ID:
        _default_sites.pop(int(tenant_id), None)


# (user id, tenant id) -> the grant a support access came from (for its audit).
_grant_of: dict[tuple[int, int], int] = {}


async def _audit_if_support(request: Any, user: Any, access: TenantAccess) -> None:
    if access.via != ACTING_VIA_SUPPORT:
        return
    grant_id = _grant_of.get((user.id, access.tenant_id))
    await support.audit_access(request, user, access.tenant_id, grant_id, access.role)


def _session_factory():
    from models import base as _base  # at call time: tests patch it

    return _base.AsyncSessionLocal


class MultiTenancy(SingleTenancy):
    """Memberships of every active tenant, plus support access."""

    async def _accesses(self, request: Any, user: Any) -> dict[int, TenantAccess]:
        state = getattr(request, "state", None)
        cache_key = ("ee-access", getattr(user, "id", None))
        cached = getattr(state, "_ee_tenancy_access", None) if state is not None else None
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        out = await self._load_accesses(user, request=request)
        if state is not None:
            try:
                state._ee_tenancy_access = (cache_key, out)
            except AttributeError:  # pragma: no cover
                pass
        return out

    async def _load_accesses(self, user: Any, *, request: Any = None) -> dict[int, TenantAccess]:
        if user is None or getattr(user, "id", None) is None:
            return {}
        if request is not None:
            memberships = await _user_memberships(request, user)
        else:
            from services.memberships import memberships_for

            async with _session_factory()() as db:
                memberships = await memberships_for(db, user.id)
        out = {t: TenantAccess(tenant_id=t, role=r, via=ACTING_VIA_MEMBERSHIP)
               for t, r in memberships.items()}
        # Cached list of active grants: no query at all while there is none.
        grants = await support.support_access_for(_session_factory(), user)
        for tid, (grant_id, role) in grants.items():
            if tid not in out:  # an explicit membership always wins
                out[tid] = TenantAccess(tenant_id=tid, role=role, via=ACTING_VIA_SUPPORT)
                _grant_of[(user.id, tid)] = grant_id
        return out

    async def resolve_access(self, request: Any, user: Any) -> TenantAccess | None:
        if user is None or getattr(user, "id", None) is None:
            return None
        accesses = await self._accesses(request, user)
        tenant_id = requested_tenant(request)
        if tenant_id is None:
            tenant_id = getattr(getattr(request, "state", None), "session_active_tenant_id", None)
        if tenant_id is None:
            if len(accesses) != 1:
                return None
            tenant_id = next(iter(accesses))
        access = accesses.get(tenant_id)
        if access is None:
            return None
        await self._ensure_default_site(tenant_id)
        await _audit_if_support(request, user, access)
        return access

    async def resolve_login_access(self, request: Any, user: Any) -> TenantAccess | None:
        access = await self.resolve_access(request, user)
        if access is not None or requested_tenant(request) is not None:
            return access
        accesses = await self._accesses(request, user)
        if not accesses:
            return None
        own = [t for t, a in accesses.items() if a.via == ACTING_VIA_MEMBERSHIP]
        chosen = await self._last_used_tenant(user, own) if own else None
        if chosen is None:
            chosen = min(own) if own else min(accesses)
        access = accesses[chosen]
        await _audit_if_support(request, user, access)
        return access

    async def _last_used_tenant(self, user: Any, candidates: list[int]) -> int | None:
        from models.settings import Session

        async with _session_factory()() as db:
            row = (await db.execute(
                select(Session.active_tenant_id)
                .where(Session.user_id == user.id, Session.active_tenant_id.in_(candidates))
                .order_by(Session.expires_at.desc())
                .limit(1)
            )).scalar_one_or_none()
        return int(row) if row is not None else None

    async def list_access_for(self, user: Any) -> list[TenantAccess]:
        accesses = await self._load_accesses(user)
        return [a for _, a in sorted(accesses.items())]

    async def effective_role(self, user: Any, tenant_id: int) -> str | None:
        access = (await self._load_accesses(user)).get(tenant_id)
        return access.role if access is not None else None

    async def active_tenant_ids(self) -> list[int]:
        """Active tenants, by id (per-tenant jobs). Suspended and deleting
        tenants are not monitored. Also refreshes the default-site cache."""
        async with _session_factory()() as db:
            with system_scope("tenancy: list active tenants"):
                rows = (await db.execute(
                    select(Tenant.id, func.min(Site.id))
                    .select_from(Tenant)
                    .outerjoin(Site, Site.tenant_id == Tenant.id)
                    .where(Tenant.status == "active")
                    .group_by(Tenant.id)
                    .order_by(Tenant.id)
                )).all()
        for tid, site_id in rows:
            remember_default_site(tid, site_id)
        return [int(tid) for tid, _ in rows]

    def is_operator(self, user: Any, role: str | None = None,
                    tenant_id: int | None = None, via: str | None = None) -> bool:
        """The ``users.is_operator`` flag, else the core rule (admin of the
        default tenant) — but only for a role held through a membership:
        an admin support grant on the default tenant gives a provider
        member that tenant's data for a while, never the installation."""
        if bool(getattr(user, "is_operator", False)):
            return True
        return super().is_operator(user, role, tenant_id, via=via)

    def can_create_tenant(self) -> bool:
        """Licence-wise (cached, no I/O). The full check — max_tenants, the
        ClickHouse rebuild, RLS — is :func:`lifecycle.creation_problems`."""
        from nodeglow_ee import license_runtime
        from nodeglow_ee.tenancy import FEATURE

        return license_runtime.manager.cached().feature_active(FEATURE)

    def default_site_id(self, tenant_id: int) -> int | None:
        return _default_sites.get(int(tenant_id))

    async def _ensure_default_site(self, tenant_id: int) -> None:
        if tenant_id in _default_sites:
            return
        async with _session_factory()() as db:
            with system_scope("tenancy: default site of a tenant"):
                site_id = (await db.execute(
                    select(func.min(Site.id)).where(Site.tenant_id == tenant_id)
                )).scalar_one_or_none()
        remember_default_site(tenant_id, site_id)
