"""Settings, User, and Session models."""
import hashlib
import hmac
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import Base, decrypt_value, encrypt_value


def _hash_token(token: str) -> str:
    """HMAC-SHA256 hash a session token for secure storage."""
    from config import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def _hash_token_legacy(token: str) -> str:
    """Legacy plain SHA256 for migration of existing sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    encrypted = Column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    # Mirror of the membership role in the default tenant, kept in sync for
    # one release so a rollback to before the auth binding (revision 043)
    # still finds every user's role. tenant_memberships is the source of truth.
    role = Column(String(16), default="admin")   # admin | editor | readonly
    auth_source = Column(String(16), default="local")  # local | ldap
    display_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Installation operator: manages providers and installation settings. No
    # implicit access to tenant data (that takes a membership).
    is_operator = Column(Boolean, nullable=False, default=False, server_default="false")


class Session(Base):
    __tablename__ = "sessions"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    # The tenant this browser session works in (set at login, changed via
    # POST /api/auth/tenant). Re-checked against the memberships on every
    # request; NULL = the user's only membership.
    active_tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="SET NULL", name="fk_sessions_active_tenant"),
        nullable=True,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

async def get_current_user(request, db: AsyncSession):
    token = request.cookies.get("nodeglow_session")
    if not token:
        return None
    now = datetime.utcnow()
    # Try HMAC hash first
    token_hash = _hash_token(token)
    result = await db.execute(
        select(Session).where(Session.token == token_hash, Session.expires_at > now)
    )
    session = result.scalar_one_or_none()
    if not session:
        # Fall back to legacy plain SHA256 and migrate if found
        legacy_hash = _hash_token_legacy(token)
        result = await db.execute(
            select(Session).where(Session.token == legacy_hash, Session.expires_at > now)
        )
        session = result.scalar_one_or_none()
        if not session:
            return None
        # Migrate legacy hash to HMAC
        session.token = token_hash
        await db.commit()
    # What the tenant resolution needs from the session (services.tenancy).
    state = getattr(request, "state", None)
    if state is not None:
        state.session_active_tenant_id = session.active_tenant_id
        state.session_token_hash = session.token
    result = await db.execute(select(User).where(User.id == session.user_id))
    return result.scalar_one_or_none()


_UNSET = object()


def _setting_tenant(db, tenant_id: int | None):
    """``(tenant, scope)`` for a tenant-scoped key: the tenant it is read or
    written for, and the context manager to run the access in.

    An explicit ``tenant_id`` (jobs in system scope, the setup wizard, the
    syslog listener) must agree with the ambient tenant context or the
    session's binding if there is one; with no context at all the access runs
    in ``tenant_scope(tenant_id)``. Without an explicit id the context (or
    the binding) decides, and with neither this raises — tenant keys fail
    closed like tenant tables.
    """
    from contextlib import nullcontext

    from services import tenancy

    if tenant_id is None:
        return tenancy.session_tenant_id(db), nullcontext()
    tenant_id = int(tenant_id)
    ctx = tenancy.current()
    if ctx is not None:
        if not ctx.system and ctx.tenant_id != tenant_id:
            raise tenancy.TenantContextError(
                f"setting for tenant {tenant_id} requested in the context of tenant {ctx.tenant_id}"
            )
        return tenant_id, nullcontext()
    bound = db.info.get(tenancy.SESSION_TENANT_KEY)
    if bound is not None and bound != tenant_id:
        raise tenancy.TenantContextError(
            f"setting for tenant {tenant_id} requested on a session bound to tenant {bound}"
        )
    return tenant_id, tenancy.tenant_scope(tenant_id)


async def _tenant_row(db, tenant_id: int, key: str):
    from models.tenant import TenantSetting

    return await db.get(TenantSetting, (tenant_id, key))


def _plain(row):
    if row.encrypted and row.value:
        return decrypt_value(row.value)
    return row.value


async def get_setting(db: AsyncSession, key: str, default=_UNSET, *, tenant_id: int | None = None):
    """Value of a settings key, routed by ``services.settings_registry``.

    Installation keys come from ``settings``; tenant keys from
    ``tenant_settings`` of the current tenant (or ``tenant_id``), falling back
    to the installation row for keys declared with ``fallback``. Without a
    stored value: ``default`` if given, else the registry default. A tenant
    key without a tenant raises ``TenantContextError``; an undeclared key
    raises ``UnknownSettingError``.
    """
    from services.settings_registry import lookup

    spec = lookup(key)
    if default is _UNSET:
        default = spec.default
    if spec.is_tenant:
        tid, scope = _setting_tenant(db, tenant_id)
        with scope:
            row = await _tenant_row(db, tid, key)
        if row is None and spec.fallback:
            row = await db.get(Setting, key)
    else:
        row = await db.get(Setting, key)
    if row is None:
        return default
    return _plain(row)


async def set_setting(
    db: AsyncSession, key: str, value: str, encrypted: bool = False, *,
    tenant_id: int | None = None, commit: bool = True,
):
    """Store a settings key where the registry says it lives.

    Commits by default (as it always did); with ``commit=False`` the change
    is flushed but left to the caller's transaction.
    """
    from services.settings_registry import lookup

    spec = lookup(key)
    stored = encrypt_value(value) if encrypted else value
    if spec.is_tenant:
        from models.tenant import TenantSetting

        tid, scope = _setting_tenant(db, tenant_id)
        with scope:
            row = await _tenant_row(db, tid, key)
            if row is None:
                row = TenantSetting(tenant_id=tid, key=key)
                db.add(row)
            row.value = stored
            row.encrypted = encrypted
            # Flush inside the scope: the new row is stamped and checked for
            # this tenant even when no context is left at the caller's commit.
            await (db.commit() if commit else db.flush())
        return
    row = await db.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.add(row)
    row.value = stored
    row.encrypted = encrypted
    if commit:
        await db.commit()


async def adopt_legacy_tenant_settings(db: AsyncSession, tenant_id: int) -> int:
    """Move tenant-scoped keys found in ``settings`` into ``tenant_settings``
    of ``tenant_id`` (an existing tenant value wins). Returns the number of
    keys moved. Revision 042 does this for upgrades; this is for data that
    arrives later in the old shape (a restored pre-042 backup). Not committed.
    """
    from models.tenant import TenantSetting
    from services import tenancy
    from services.settings_registry import tenant_keys

    rows = (await db.execute(select(Setting).where(Setting.key.in_(tenant_keys())))).scalars().all()
    if not rows:
        return 0
    with tenancy.system_scope(f"settings: adopt pre-split settings into tenant {tenant_id}"):
        for row in rows:
            if await db.get(TenantSetting, (tenant_id, row.key)) is None:
                db.add(TenantSetting(tenant_id=tenant_id, key=row.key, value=row.value,
                                     encrypted=bool(row.encrypted)))
            await db.delete(row)
        await db.flush()
    return len(rows)


async def is_setup_complete(db: AsyncSession) -> bool:
    val = await get_setting(db, "setup_complete")
    return val == "true"
