import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Session, User, get_db, get_current_user
from models.settings import _hash_token, _hash_token_legacy, get_setting, set_setting
from ratelimit import rate_limit
from services.audit import log_action

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_DAYS = 7

# Account lockout configuration
_LOCKOUT_ATTEMPTS = 5
_LOCKOUT_WINDOW = 900  # 15 minutes


async def _get_failed_attempts(db: AsyncSession, username: str) -> list[float]:
    """Get failed login timestamps from DB."""
    raw = await get_setting(db, f"_lockout:{username}", "[]")
    try:
        return json.loads(raw)
    except Exception:
        return []


async def _record_failed_attempt(db: AsyncSession, username: str):
    """Record a failed login attempt in the DB."""
    now = time.time()
    attempts = await _get_failed_attempts(db, username)
    attempts = [t for t in attempts if t > now - _LOCKOUT_WINDOW]
    attempts.append(now)
    await set_setting(db, f"_lockout:{username}", json.dumps(attempts))


async def _clear_failed_attempts(db: AsyncSession, username: str):
    """Clear failed login attempts for a user."""
    await set_setting(db, f"_lockout:{username}", "[]")


async def _is_locked_out(db: AsyncSession, username: str) -> bool:
    """Check if a user account is locked out."""
    now = time.time()
    attempts = await _get_failed_attempts(db, username)
    recent = [t for t in attempts if t > now - _LOCKOUT_WINDOW]
    return len(recent) >= _LOCKOUT_ATTEMPTS


class LoginRequest(BaseModel):
    username: str
    password: str


async def _get_ldap_config(db: AsyncSession):
    """Build LdapConfig from settings, or None if LDAP is disabled."""
    from models.base import decrypt_value
    enabled = await get_setting(db, "ldap_enabled", "0")
    if enabled != "1":
        return None

    from services.ldap_auth import LdapConfig
    bind_pw_enc = await get_setting(db, "ldap_bind_password", "")
    try:
        bind_pw = decrypt_value(bind_pw_enc) if bind_pw_enc else ""
    except Exception:
        bind_pw = bind_pw_enc

    return LdapConfig(
        server=await get_setting(db, "ldap_server", ""),
        bind_dn=await get_setting(db, "ldap_bind_dn", ""),
        bind_password=bind_pw,
        base_dn=await get_setting(db, "ldap_base_dn", ""),
        user_filter=await get_setting(db, "ldap_user_filter",
                                      "(&(objectClass=person)(sAMAccountName={username}))"),
        display_attr=await get_setting(db, "ldap_display_attr", "displayName"),
        group_attr=await get_setting(db, "ldap_group_attr", "memberOf"),
        admin_group=await get_setting(db, "ldap_admin_group", ""),
        editor_group=await get_setting(db, "ldap_editor_group", ""),
        use_ssl=(await get_setting(db, "ldap_use_ssl", "0")) == "1",
        start_tls=(await get_setting(db, "ldap_start_tls", "0")) == "1",
    )


async def _try_ldap_login(db: AsyncSession, username: str, password: str):
    """Attempt LDAP auth. Returns (User, created) or (None, False)."""
    ldap_cfg = await _get_ldap_config(db)
    if not ldap_cfg or not ldap_cfg.server:
        return None, False

    from services.ldap_auth import authenticate_ldap
    ldap_user = await authenticate_ldap(ldap_cfg, username, password)
    if not ldap_user:
        return None, False

    # Find or create local user
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user:
        # Update role and display name from LDAP
        changed = False
        if user.auth_source != "ldap":
            user.auth_source = "ldap"
            changed = True
        if ldap_user.role and user.role != ldap_user.role:
            user.role = ldap_user.role
            changed = True
        if ldap_user.display_name and user.display_name != ldap_user.display_name:
            user.display_name = ldap_user.display_name
            changed = True
        if changed:
            await db.flush()
        return user, False
    else:
        # Auto-create user from LDAP
        placeholder_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt(rounds=12)).decode()
        user = User(
            username=username,
            password_hash=placeholder_hash,
            role=ldap_user.role,
            auth_source="ldap",
            display_name=ldap_user.display_name,
        )
        db.add(user)
        await db.flush()
        logger.info("Auto-created LDAP user: %s (role=%s)", username, ldap_user.role)
        return user, True


def _create_session_response(user, token: str, request):
    """Build JSON response with session cookie."""
    response = JSONResponse({
        "ok": True,
        "user": {"id": user.id, "username": user.username, "role": user.role},
    })
    force_secure = os.environ.get("SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        "nodeglow_session", token,
        max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict",
        secure=force_secure or is_https,
    )
    return response


@router.post("/api/auth/login")
@rate_limit(max_requests=10, window_seconds=60)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # Account lockout check (DB-persisted, survives restarts)
    if await _is_locked_out(db, body.username):
        return JSONResponse({"error": "Account temporarily locked. Try again later."}, status_code=429)

    # Try LDAP first (if enabled)
    ldap_user, _ = await _try_ldap_login(db, body.username, body.password)
    if ldap_user:
        await _clear_failed_attempts(db, body.username)
        token = secrets.token_hex(32)
        db.add(Session(token=_hash_token(token), user_id=ldap_user.id,
                       expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS)))
        await log_action(db, request, "auth.login", "user", ldap_user.id, ldap_user.username,
                         details={"method": "ldap"})
        await db.commit()
        return _create_session_response(ldap_user, token, request)

    # Fall back to local auth
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # Skip local auth for LDAP-only users (no valid local password)
    if user and user.auth_source == "ldap":
        await _record_failed_attempt(db, body.username)
        return JSONResponse({"error": "LDAP authentication failed"}, status_code=401)

    _dummy_hash = b"$2b$12$000000000000000000000uGHEjmFMntPDYjXJPBT3V44YS5gL0nS"
    stored_hash = user.password_hash.encode() if user else _dummy_hash
    pw_ok = bcrypt.checkpw(body.password.encode(), stored_hash)
    if not user or not pw_ok:
        await _record_failed_attempt(db, body.username)
        return JSONResponse({"error": "Invalid username or password"}, status_code=401)

    # Clear failed attempts on success
    await _clear_failed_attempts(db, body.username)
    token = secrets.token_hex(32)
    db.add(Session(token=_hash_token(token), user_id=user.id,
                   expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS)))
    await log_action(db, request, "auth.login", "user", user.id, user.username,
                     details={"method": "local"})
    await db.commit()
    return _create_session_response(user, token, request)


@router.get("/api/auth/me")
async def get_current_user_api(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user:
        return JSONResponse({"user": None}, status_code=401)
    return {"user": {"id": user.id, "username": user.username, "role": user.role or "admin"}}


@router.post("/api/auth/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("nodeglow_session")
    if token:
        session = await db.get(Session, _hash_token(token))
        if not session:
            # Fall back to legacy plain SHA256
            session = await db.get(Session, _hash_token_legacy(token))
        if session:
            await db.delete(session)
            await log_action(db, request, "auth.logout")
            await db.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie("nodeglow_session")
    return response
