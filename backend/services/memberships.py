"""Tenant memberships: who may work in which tenant, with which role.

Since the auth binding (multi-tenancy phase 4, revision 043) the membership
is the source of truth for a user's role. ``users.role`` mirrors the role in
the default tenant for one release, so rolling back to before 043 still
finds every role; nothing reads it for authorisation any more.

Every path that creates or changes a user goes through here: the setup
wizard, the users API (and its form twins), LDAP login, backup restore.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from models.settings import User
from models.tenant import DEFAULT_TENANT_ID, Tenant, TenantMembership

VALID_ROLES = ("admin", "editor", "readonly")


def normalise_role(role: str | None, default: str = "readonly") -> str:
    return role if role in VALID_ROLES else default


async def memberships_for(db, user_id: int, *, active_only: bool = True) -> dict[int, str]:
    """``{tenant_id: role}`` of the user (only tenants with status active)."""
    stmt = select(TenantMembership.tenant_id, TenantMembership.role).where(
        TenantMembership.user_id == user_id
    )
    if active_only:
        stmt = stmt.join(Tenant, Tenant.id == TenantMembership.tenant_id).where(
            Tenant.status == "active"
        )
    rows = (await db.execute(stmt)).all()
    return {int(t): normalise_role(r) for t, r in rows}


async def membership(db, user_id: int, tenant_id: int) -> TenantMembership | None:
    return await db.get(TenantMembership, (user_id, tenant_id))


def _mirror_role(user: User, tenant_id: int, role: str) -> None:
    # users.role mirrors the default tenant's membership (rollback safety).
    if tenant_id == DEFAULT_TENANT_ID:
        user.role = role


async def grant(db, user: User, tenant_id: int, role: str, *, created_by: int | None = None) -> TenantMembership:
    """Create or update the user's membership in ``tenant_id``. Flushes, does
    not commit. The user must have an id (flush it first)."""
    role = normalise_role(role)
    if user.id is None:
        await db.flush()
    row = await membership(db, user.id, tenant_id)
    if row is None:
        row = TenantMembership(user_id=user.id, tenant_id=tenant_id, role=role,
                               created_at=datetime.utcnow(), created_by=created_by)
        db.add(row)
    else:
        row.role = role
    _mirror_role(user, tenant_id, role)
    await db.flush()
    return row


async def revoke(db, user_id: int, tenant_id: int) -> bool:
    """Remove the membership. True if there was one. Flushes."""
    row = await membership(db, user_id, tenant_id)
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


async def tenant_members(db, tenant_id: int) -> list[tuple[User, str]]:
    """(user, role) of every member of the tenant, oldest account first."""
    rows = (await db.execute(
        select(User, TenantMembership.role)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(User.created_at, User.id)
    )).all()
    return [(u, normalise_role(r)) for u, r in rows]


async def admin_count(db, tenant_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(TenantMembership)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.role == "admin")
    )).scalar() or 0


async def backfill_memberships(db, tenant_id: int = DEFAULT_TENANT_ID) -> int:
    """Give every user without any membership one in ``tenant_id`` with the
    role from ``users.role`` (what revisions 039/043 do), e.g. after restoring
    a backup taken before memberships existed. Returns the number created."""
    has_any = select(TenantMembership.user_id).where(TenantMembership.user_id == User.id).exists()
    users = (await db.execute(select(User).where(~has_any))).scalars().all()
    for u in users:
        db.add(TenantMembership(user_id=u.id, tenant_id=tenant_id,
                                role=normalise_role(u.role or "admin", "admin"),
                                created_at=datetime.utcnow()))
    if users:
        await db.flush()
    return len(users)
