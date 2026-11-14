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


class DeleteRefused(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


LAST_ADMIN = ("Deleting this provider would leave the tenant without an admin: every admin "
              "signs in through it. Make a local account an admin first.")
OWN_ACCESS = ("You sign in through this provider only. Deleting it would remove your own "
              "access; ask another admin, or sign in with a local account.")


async def _losing_membership(db, idp: IdentityProvider, user_ids: list[int]) -> list[User]:
    """The single sign-on users that exist in this tenant only through
    ``idp``: deleting it takes their membership away."""
    out: list[User] = []
    for uid in user_ids:
        user = await db.get(User, uid)
        if user is None or (user.auth_source or "local") != AUTH_SOURCE_OIDC:
            continue
        # Identities of other tenants are not visible in this tenant's scope.
        with system_scope("sso: does a single sign-on user have other identities"):
            others = (await db.execute(
                select(func.count()).select_from(UserIdentity)
                .where(UserIdentity.user_id == uid, UserIdentity.identity_provider_id != idp.id)
            )).scalar() or 0
        if not others:
            out.append(user)
    return out


async def delete_provider(db, idp: IdentityProvider, *, acting_user_id: int | None) -> Removed:
    """Delete ``idp`` and what hangs on it (request's tenant scope). Not committed.

    Refused (:class:`DeleteRefused`, nothing changed) when it would take away
    the acting user's own membership, or leave the tenant without an admin —
    the same rules ``routers.users`` applies to removing a user."""
    removed = Removed()
    tenant_id = idp.tenant_id
    user_ids = sorted(set((await db.execute(
        select(UserIdentity.user_id).where(UserIdentity.identity_provider_id == idp.id)
    )).scalars().all()))
    losing = await _losing_membership(db, idp, user_ids)
    losing_ids = {u.id for u in losing}
    if acting_user_id is not None and acting_user_id in losing_ids:
        raise DeleteRefused(OWN_ACCESS)
    if losing_ids:
        admins = set((await db.execute(
            select(TenantMembership.user_id).where(TenantMembership.tenant_id == tenant_id,
                                                   TenantMembership.role == "admin")
        )).scalars().all())
        if admins and admins <= losing_ids:
            raise DeleteRefused(LAST_ADMIN)

    removed.identities = len(user_ids)
    removed.sessions = (await db.execute(
        delete(Session).where(Session.identity_provider_id == idp.id)
    )).rowcount or 0
    await db.execute(delete(SsoFlow).where(SsoFlow.identity_provider_id == idp.id))
    await db.execute(delete(UserIdentity).where(UserIdentity.identity_provider_id == idp.id))
    await db.delete(idp)
    await db.flush()

    for user in losing:
        uid = user.id
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
