"""User management of the request's tenant.

Users are global identities; what this router manages is their membership
in the request's tenant (``tenant_memberships``, the source of truth for the
role since the auth binding). In the core there is one tenant, so this is
exactly the old user management. ``users.role`` is kept as a mirror of the
default tenant's role for one release (rollback safety, see
``services.memberships``).
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import User, get_db
from models.settings import Session as UserSession, _hash_token, _hash_token_legacy
from models.tenant import ProviderMembership, TenantMembership
from ratelimit import rate_limit
from services import memberships as ms
from services.tenancy import (
    SUPPORT_IAM_REFUSED, request_is_admin, request_is_operator, request_via_support,
)
from utils.password import hash_password, validate_password, verify_password

router = APIRouter(prefix="/users")
api_router = APIRouter()

VALID_ROLES = set(ms.VALID_ROLES)


def _require_admin(request: Request) -> bool:
    """Return True (= blocked) if the current user is not an admin of the
    request's tenant."""
    return not request_is_admin(request)


def _refuse_change(request: Request) -> JSONResponse | None:
    """403 unless the request may change who has access to its tenant: an
    admin, and not through support access — a support session could
    otherwise leave an account behind that outlives its grant (or take the
    tenant's own admins out)."""
    if not getattr(request.state, "current_user", None) or not request_is_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    if request_via_support(request):
        return JSONResponse({"error": SUPPORT_IAM_REFUSED, "code": "support_access"}, status_code=403)
    return None


def _tenant(request: Request) -> int:
    return request.state.tenant_id


FOREIGN_IDENTITY = ("This user also has access outside this tenant (another tenant, the "
                    "provider or the installation); only the user or an operator can change "
                    "their credentials.")


async def _reach(db: AsyncSession, request: Request, user: User) -> set[str]:
    """Where else this (global) identity has access, besides the request's
    tenant: ``operator`` (the installation), ``provider`` (a provider's
    tenants), ``tenant`` (another tenant's membership). Empty: the request's
    tenant alone owns the account."""
    reach: set[str] = set()
    if bool(getattr(user, "is_operator", False)):
        reach.add("operator")
    other = (await db.execute(
        select(TenantMembership.tenant_id).where(
            TenantMembership.user_id == user.id, TenantMembership.tenant_id != _tenant(request))
        .limit(1)
    )).first()
    if other is not None:
        reach.add("tenant")
    provider = (await db.execute(
        select(ProviderMembership.provider_id).where(ProviderMembership.user_id == user.id).limit(1)
    )).first()
    if provider is not None:
        reach.add("provider")
    return reach


async def _may_set_credentials(db: AsyncSession, request: Request, user: User) -> bool:
    """Whether the request may reset this user's password. Users are global:
    a tenant admin may only for an account the tenant alone owns — else one
    customer's admin could take over an account that reaches other tenants,
    the operator's included. An installation operator may for anyone (in the
    core: the admin of the one tenant, so nothing changes there)."""
    if request_is_operator(request):
        return True
    return not await _reach(db, request, user)


def _verify_password(user, candidate) -> bool:
    """True if ``candidate`` is the user's current local password.

    LDAP users have a random placeholder hash, so this is always False for
    them — their password lives in the directory, not here.
    """
    if not candidate or not isinstance(candidate, str):
        return False
    if (getattr(user, "auth_source", "local") or "local") == "ldap":
        return False
    try:
        return verify_password(candidate, user.password_hash or "")
    except ValueError:
        return False


async def _member(db: AsyncSession, request: Request, user_id: int) -> tuple[User | None, str | None]:
    """The user and their role, if they are a member of the request's tenant.
    A user of another tenant is "not found" here."""
    user = await db.get(User, user_id)
    if user is None:
        return None, None
    m = await ms.membership(db, user_id, _tenant(request))
    if m is None:
        return None, None
    return user, ms.normalise_role(m.role)


async def _remove_from_tenant(db: AsyncSession, request: Request, user: User) -> None:
    """Revoke the membership; a user left without any membership is deleted
    (in the core: always — there is one tenant). An operator or a provider
    member keeps the account: it is not the tenant's to delete (an operator
    may delete another operator's, as in the core)."""
    reach = await _reach(db, request, user)
    keep = "provider" in reach or ("operator" in reach and not request_is_operator(request))
    await ms.revoke(db, user.id, _tenant(request))
    remaining = (await db.execute(
        select(TenantMembership.tenant_id).where(TenantMembership.user_id == user.id)
    )).first()
    if remaining is None and not keep:
        await db.execute(sa_delete(UserSession).where(UserSession.user_id == user.id))
        await db.delete(user)


async def _is_last_admin(db: AsyncSession, request: Request, role: str) -> bool:
    return role == "admin" and await ms.admin_count(db, _tenant(request)) <= 1


def _user_json(u: User, role: str, *, full: bool = True) -> dict:
    out = {"id": u.id, "username": u.username, "role": role}
    if full:
        out["auth_source"] = getattr(u, "auth_source", "local") or "local"
        out["display_name"] = getattr(u, "display_name", None)
    out["created_at"] = u.created_at.isoformat() if u.created_at else None
    return out


@api_router.get("/api/users")
async def list_users_api(request: Request, db: AsyncSession = Depends(get_db)):
    """JSON user list (members of the request's tenant) for the frontend."""
    if not getattr(request.state, "current_user", None) or _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    members = await ms.tenant_members(db, _tenant(request))
    return JSONResponse([_user_json(u, role) for u, role in members])


@api_router.post("/api/users")
async def create_user_api(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new user in the request's tenant (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if err := _refuse_change(request):
        return err
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password", "")
    role = body.get("role", "readonly")
    if not username or not password:
        return JSONResponse({"error": "Username and password required"}, status_code=400)
    pw_error = validate_password(password)
    if pw_error:
        return JSONResponse({"error": pw_error}, status_code=400)
    if role not in VALID_ROLES:
        role = "readonly"
    existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if existing:
        return JSONResponse({"error": "Username already exists"}, status_code=409)
    pw_hash = hash_password(password)
    user = User(username=username, password_hash=pw_hash, role=role)
    db.add(user)
    await db.flush()
    await ms.grant(db, user, _tenant(request), role, created_by=getattr(current, "id", None))
    await db.commit()
    return JSONResponse({"ok": True, "id": user.id, "username": user.username, "role": role})


@api_router.delete("/api/users/{user_id}")
async def delete_user_api(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Remove a user from the request's tenant (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if err := _refuse_change(request):
        return err
    if current.id == user_id:
        return JSONResponse({"error": "Cannot delete yourself"}, status_code=400)
    user, role = await _member(db, request, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if await _is_last_admin(db, request, role):
        return JSONResponse({"error": "Cannot delete the last admin"}, status_code=400)
    await _remove_from_tenant(db, request, user)
    await db.commit()
    return JSONResponse({"ok": True})


@api_router.patch("/api/users/{user_id}")
@rate_limit(max_requests=5, window_seconds=300)
async def update_user_api(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Update user role (in the request's tenant) or password (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if err := _refuse_change(request):
        return err
    user, role = await _member(db, request, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    body = await request.json()
    resetting_other = bool(body.get("password")) and user.id != current.id
    if resetting_other and not await _may_set_credentials(db, request, user):
        return JSONResponse({"error": FOREIGN_IDENTITY, "code": "foreign_identity"}, status_code=403)
    if "role" in body:
        new_role = body["role"]
        if new_role not in VALID_ROLES:
            return JSONResponse({"error": "Invalid role"}, status_code=400)
        if new_role != "admin" and await _is_last_admin(db, request, role):
            return JSONResponse({"error": "Cannot demote the last admin"}, status_code=400)
        await ms.grant(db, user, _tenant(request), new_role)
    if "password" in body and body["password"]:
        # An admin resetting SOMEONE ELSE needs no old password. Changing
        # one's own does: otherwise a hijacked session (or an unlocked
        # screen) is enough to take the account over for good.
        if user.id == current.id and not _verify_password(user, body.get("current_password")):
            return JSONResponse({"error": "Current password is incorrect",
                                 "code": "current_password_required"}, status_code=403)
        pw_error = validate_password(body["password"])
        if pw_error:
            return JSONResponse({"error": pw_error}, status_code=400)
        user.password_hash = hash_password(body["password"])
        await db.execute(sa_delete(UserSession).where(UserSession.user_id == user_id))
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/me/password")
@rate_limit(max_requests=5, window_seconds=300)
async def change_own_password(
    request: Request,
    password: str = Form(...),
    current_password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Allow any logged-in user to change their own password — after proving
    they know the current one."""
    user = request.state.current_user
    if not user:
        # (No local import here: it made RedirectResponse a local name for the
        # whole function, and the success path below raised UnboundLocalError.)
        return RedirectResponse(url="/login", status_code=303)
    db_user = await db.get(User, user.id)
    if not db_user or not _verify_password(db_user, current_password):
        return JSONResponse({"error": "Current password is incorrect",
                             "code": "current_password_required"}, status_code=403)
    pw_error = validate_password(password)
    if pw_error:
        return JSONResponse({"error": pw_error}, status_code=400)
    if db_user:
        db_user.password_hash = hash_password(password)
        # Invalidate all other sessions (keep current one via new login)
        current_token = request.cookies.get("nodeglow_session")
        if current_token:
            current_hmac = _hash_token(current_token)
            current_legacy = _hash_token_legacy(current_token)
            await db.execute(
                sa_delete(UserSession).where(
                    UserSession.user_id == user.id,
                    UserSession.token != current_hmac,
                    UserSession.token != current_legacy,
                )
            )
        await db.commit()
    # Redirect back to referrer (validated) or dashboard
    ref = request.headers.get("referer", "/")
    if not ref.startswith("/") or ref.startswith("//"):
        ref = "/"
    return RedirectResponse(url=ref, status_code=303)


@router.get("")
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    if _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    members = await ms.tenant_members(db, _tenant(request))
    return JSONResponse([_user_json(u, role, full=False) for u, role in members])


@router.post("/add")
async def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("readonly"),
    db: AsyncSession = Depends(get_db),
):
    if err := _refuse_change(request):
        return err
    pw_error = validate_password(password)
    if pw_error:
        return JSONResponse({"error": pw_error}, status_code=400)
    if role not in VALID_ROLES:
        role = "readonly"
    existing = (await db.execute(select(User).where(User.username == username.strip()))).scalar_one_or_none()
    if existing:
        return RedirectResponse(url="/users?error=exists", status_code=303)
    pw_hash = hash_password(password)
    user = User(username=username.strip(), password_hash=pw_hash, role=role)
    db.add(user)
    await db.flush()
    current = getattr(request.state, "current_user", None)
    await ms.grant(db, user, _tenant(request), role, created_by=getattr(current, "id", None))
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/role")
async def update_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if err := _refuse_change(request):
        return err
    if role not in VALID_ROLES:
        return RedirectResponse(url="/users?error=invalid_role", status_code=303)
    user, current_role = await _member(db, request, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if role != "admin" and await _is_last_admin(db, request, current_role):
        return RedirectResponse(url="/users?error=last_admin", status_code=303)
    await ms.grant(db, user, _tenant(request), role)
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/password")
@rate_limit(max_requests=5, window_seconds=300)
async def reset_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if err := _refuse_change(request):
        return err
    pw_error = validate_password(password)
    if pw_error:
        return JSONResponse({"error": pw_error}, status_code=400)
    user, _ = await _member(db, request, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    current = request.state.current_user
    if user.id != current.id and not await _may_set_credentials(db, request, user):
        return JSONResponse({"error": FOREIGN_IDENTITY, "code": "foreign_identity"}, status_code=403)
    user.password_hash = hash_password(password)
    await db.execute(sa_delete(UserSession).where(UserSession.user_id == user_id))
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/delete")
async def delete_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if err := _refuse_change(request):
        return err
    current = request.state.current_user
    if current and current.id == user_id:
        return RedirectResponse(url="/users?error=self_delete", status_code=303)
    user, role = await _member(db, request, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if await _is_last_admin(db, request, role):
        return RedirectResponse(url="/users?error=last_admin", status_code=303)
    await _remove_from_tenant(db, request, user)
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)
