"""What deleting a provider removes.

The foreign keys cascade on PostgreSQL; this does the same explicitly, so
the result does not depend on the database enforcing them (the test suite's
SQLite does not), and so the users who only existed through the provider are
handled like a user removed from the tenant in ``routers.users``:

* the sessions opened through the provider end;
* its pending flows and its linked identities go;
* a single sign-on user (``auth_source = 'oidc'``) left without any linked
  identity loses the membership in this tenant, and is deleted when no
  membership is left (unless an operator or provider member — not the
  tenant's to delete).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select

from models.identity import AUTH_SOURCE_OIDC, IdentityProvider, SsoFlow, UserIdentity
from models.settings import Session, User
from models.tenant import ProviderMembership, TenantMembership
from services import memberships as ms
from services.tenancy import system_scope


@dataclass
class Removed:
    identities: int = 0
    sessions: int = 0
    memberships_revoked: int = 0
    users_deleted: int = 0


async def affected_users(db, idp: IdentityProvider) -> int:
    """People linked to the provider (shown before a delete)."""
    return (await db.execute(
        select(func.count()).select_from(UserIdentity)
        .where(UserIdentity.identity_provider_id == idp.id)
    )).scalar() or 0


async def delete_provider(db, idp: IdentityProvider) -> Removed:
    """Delete ``idp`` and what hangs on it (request's tenant scope). Not committed."""
    removed = Removed()
    tenant_id = idp.tenant_id
    user_ids = sorted(set((await db.execute(
        select(UserIdentity.user_id).where(UserIdentity.identity_provider_id == idp.id)
    )).scalars().all()))
    removed.identities = len(user_ids)
    removed.sessions = (await db.execute(
        delete(Session).where(Session.identity_provider_id == idp.id)
    )).rowcount or 0
    await db.execute(delete(SsoFlow).where(SsoFlow.identity_provider_id == idp.id))
    await db.execute(delete(UserIdentity).where(UserIdentity.identity_provider_id == idp.id))
    await db.delete(idp)
    await db.flush()

    for uid in user_ids:
        user = await db.get(User, uid)
        if user is None or (user.auth_source or "local") != AUTH_SOURCE_OIDC:
            continue
        # Identities of other tenants are not visible in this tenant's scope.
        with system_scope("sso: does a single sign-on user have other identities"):
            others = (await db.execute(
                select(func.count()).select_from(UserIdentity).where(UserIdentity.user_id == uid)
            )).scalar() or 0
        if others:
            continue
        if await ms.revoke(db, uid, tenant_id):
            removed.memberships_revoked += 1
        remaining = (await db.execute(
            select(func.count()).select_from(TenantMembership).where(TenantMembership.user_id == uid)
        )).scalar() or 0
        provider_member = (await db.execute(
            select(func.count()).select_from(ProviderMembership)
            .where(ProviderMembership.user_id == uid)
        )).scalar() or 0
        if not remaining and not provider_member and not user.is_operator:
            await db.execute(delete(Session).where(Session.user_id == uid))
            await db.delete(user)
            removed.users_deleted += 1
    await db.flush()
    return removed
