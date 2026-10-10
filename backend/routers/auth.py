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
from ratelimit import rate_limit, failed_auth_throttled
from services.audit import log_action
from utils.password import verify_password

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_DAYS = 7

# ── Brute-force protection: soft lock + trusted-device bypass ────────────────
#
# The old hard lock (5 failures per username in 15 min → 429 for EVERYONE)
# was a lockout button: anyone could keep the admin out for good by sending
# five wrong passwords every 15 minutes. And since every request reached the
# backend from the frontend container's address, the IP throttle was global
# too.
#
# Now (OWASP "device cookie" pattern):
#   * Failures are counted per username (DB-persisted, survives restarts).
#   * Past the thresholds the account is soft-locked for unknown clients:
#     5 failures in 15 min, or 20 in 24 h (so a slow, patient attacker is
#     throttled progressively harder rather than getting 5 fresh tries every
#     quarter hour). Attempts refused by the lock are not counted, so the
#     lock cannot be stretched indefinitely by hammering it.
#   * A browser that has signed in to this account before carries a signed
#     device cookie and is NOT subject to the soft lock — an attacker
#     cannot lock the real admin out of their own browser. Each device
#     cookie has its own failure budget, so a stolen cookie is no free pass.
#   * A successful login from a trusted device while the account is locked
#     does not reset the counter (that would hand the attacker fresh tries).
#   * The per-IP throttle in ratelimit.py still caps spraying from one host.
_LOCKOUT_ATTEMPTS = 5
_LOCKOUT_WINDOW = 900            # 15 minutes
_LOCKOUT_LONG_ATTEMPTS = 20
_LOCKOUT_LONG_WINDOW = 86400     # 24 hours
_MAX_STORED_FAILURES = 100

_DEVICE_COOKIE_PREFIX = "ng_dev_"
_DEVICE_COOKIE_DAYS = 180
_DEVICE_MAX_FAILURES = 5
_DEVICE_FAILURE_WINDOW = 900

_now = time.time  # monkeypatched by tests


def _lockout_key(username: str) -> str:
    return f"_lockout:{(username or '').strip().lower()}"


async def _get_failed_attempts(db: AsyncSession, username: str) -> list[float]:
    """Get failed login timestamps from DB."""
    raw = await get_setting(db, _lockout_key(username), "[]")
    try:
        return [float(t) for t in json.loads(raw)]
    except Exception:
        return []


async def _record_failed_attempt(db: AsyncSession, username: str):
    """Record a failed login attempt in the DB."""
    now = _now()
    attempts = await _get_failed_attempts(db, username)
    attempts = [t for t in attempts if t > now - _LOCKOUT_LONG_WINDOW]
    attempts.append(now)
    await set_setting(db, _lockout_key(username), json.dumps(attempts[-_MAX_STORED_FAILURES:]))


async def _clear_failed_attempts(db: AsyncSession, username: str):
    """Clear failed login attempts for a user."""
    await set_setting(db, _lockout_key(username), "[]")


def _lock_remaining(attempts: list[float], now: float) -> int:
    """Seconds until the soft lock lifts; 0 if not locked."""
    remaining = 0
    for limit, window in ((_LOCKOUT_ATTEMPTS, _LOCKOUT_WINDOW),
                          (_LOCKOUT_LONG_ATTEMPTS, _LOCKOUT_LONG_WINDOW)):
        recent = sorted(t for t in attempts if t > now - window)
        if len(recent) >= limit:
            # Unlocks when enough of the oldest failures age out of the window.
            unlock_at = recent[len(recent) - limit] + window
            remaining = max(remaining, int(unlock_at - now) + 1)
    return remaining


async def _is_locked_out(db: AsyncSession, username: str) -> bool:
    """Check if a user account is soft-locked (for clients without a device cookie)."""
    return _lock_remaining(await _get_failed_attempts(db, username), _now()) > 0


def _device_cookie_name(username: str) -> str:
    import hashlib
    digest = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()[:12]
    return f"{_DEVICE_COOKIE_PREFIX}{digest}"


def _sign_device(username: str, nonce: str) -> str:
    import hashlib
    import hmac
    from config import SECRET_KEY
    msg = f"device|{(username or '').strip().lower()}|{nonce}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def _trusted_device_nonce(request: Request, username: str) -> str | None:
    """The nonce of a valid device cookie for this username, else None."""
    import hmac
    raw = request.cookies.get(_device_cookie_name(username))
    if not raw or "." not in raw:
        return None
    nonce, sig = raw.rsplit(".", 1)
    if not nonce or not hmac.compare_digest(sig, _sign_device(username, nonce)):
        return None
    return nonce


def _device_failure_key(nonce: str) -> str:
    return f"login_device_fail:{nonce}"


async def _device_exhausted(nonce: str) -> bool:
    from services import shared_state
    return await shared_state.window_count(_device_failure_key(nonce), _DEVICE_FAILURE_WINDOW) \
        >= _DEVICE_MAX_FAILURES


async def _record_device_failure(nonce: str | None):
    if nonce:
        from services import shared_state
        await shared_state.incr_window(_device_failure_key(nonce), _DEVICE_FAILURE_WINDOW)


def _set_device_cookie(response, request: Request, username: str, nonce: str | None):
    nonce = nonce or secrets.token_hex(16)
    force_secure = os.environ.get("SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        _device_cookie_name(username), f"{nonce}.{_sign_device(username, nonce)}",
        max_age=_DEVICE_COOKIE_DAYS * 86400, httponly=True, samesite="strict",
        secure=force_secure or is_https,
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class LdapConfigError(Exception):
    """LDAP is enabled but its stored configuration cannot be used."""


async def _get_ldap_config(db: AsyncSession):
    """Build LdapConfig from settings, or None if LDAP is disabled.

    Raises LdapConfigError when the stored bind password cannot be decrypted.
    It used to fall back to sending the ciphertext itself as the bind
    password — to whichever server is configured — and to keep going.
    """
    from models.base import decrypt_value
    enabled = await get_setting(db, "ldap_enabled", "0")
    if enabled != "1":
        return None

    from services.ldap_auth import LdapConfig
    bind_pw_enc = await get_setting(db, "ldap_bind_password", "")
    try:
        bind_pw = decrypt_value(bind_pw_enc) if bind_pw_enc else ""
    except Exception:
        logger.error(
            "LDAP bind password cannot be decrypted (SECRET_KEY changed?). "
            "LDAP login is disabled until the bind password is saved again."
        )
        raise LdapConfigError("Stored LDAP bind password cannot be decrypted — save it again")

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
        tls_verify=(await get_setting(db, "ldap_tls_verify", "1")) != "0",
    )


async def _try_ldap_login(db: AsyncSession, username: str, password: str):
    """Attempt LDAP auth. Returns (User, created) or (None, False)."""
    try:
        ldap_cfg = await _get_ldap_config(db)
    except LdapConfigError:
        return None, False  # fail closed; already logged
    if not ldap_cfg or not ldap_cfg.server:
        return None, False

    # A same-named LOCAL account is never taken over by a directory login.
    # It used to be: LDAP auth succeeded, the local row was flipped to
    # auth_source="ldap" and kept its role — so whoever controls the
    # directory entry "admin" became Nodeglow's local admin. Such a user
    # keeps logging in with the local password; resolving the clash is an
    # admin decision (rename or delete the local account).
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is not None and (user.auth_source or "local") != "ldap":
        return None, False

    from services.ldap_auth import authenticate_ldap
    ldap_user = await authenticate_ldap(ldap_cfg, username, password)
    if not ldap_user:
        return None, False

    if user:
        # Update role and display name from LDAP
        changed = False
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
    # IP-scoped failed-auth throttle — bounds username-spraying from one host,
    # independent of the per-username soft lock below. Counts this attempt.
    # (The client IP is real only when uvicorn trusts the proxy in front of
    # it — see FORWARDED_ALLOW_IPS in the Dockerfile.)
    if failed_auth_throttled(request):
        return JSONResponse({"error": "Too many attempts. Try again later."}, status_code=429)

    # Soft lock (see the block comment at the top). A browser holding this
    # account's device cookie is exempt unless that cookie burned its own
    # failure budget.
    device_nonce = _trusted_device_nonce(request, body.username)
    if device_nonce and await _device_exhausted(device_nonce):
        device_nonce = None
    failures = await _get_failed_attempts(db, body.username)
    lock_remaining = _lock_remaining(failures, _now())
    if lock_remaining and not device_nonce:
        minutes = max(1, (lock_remaining + 59) // 60)
        return JSONResponse(
            {"error": (
                "Too many failed sign-in attempts for this account. Try again in "
                f"{minutes} minute{'s' if minutes != 1 else ''}, or sign in from a "
                "browser you have used with this account before."
            )},
            status_code=429,
            headers={"Retry-After": str(lock_remaining)},
        )

    async def _fail(message: str = "Invalid username or password"):
        await _record_failed_attempt(db, body.username)
        await _record_device_failure(device_nonce)
        return JSONResponse({"error": message}, status_code=401)

    async def _succeed(user, method: str):
        # Resetting the counter while locked would give an attacker who keeps
        # failing a fresh budget every time the real user signs in.
        if not lock_remaining:
            await _clear_failed_attempts(db, body.username)
        token = secrets.token_hex(32)
        db.add(Session(token=_hash_token(token), user_id=user.id,
                       expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS)))
        await log_action(db, request, "auth.login", "user", user.id, user.username,
                         details={"method": method, "trusted_device": bool(device_nonce)})
        await db.commit()
        response = _create_session_response(user, token, request)
        _set_device_cookie(response, request, body.username, device_nonce)
        return response

    # Reject empty/whitespace-only passwords before any auth attempt. An empty
    # password against an LDAP server triggers an unauthenticated bind, which
    # most directories accept — bypassing authentication. Treat it exactly like
    # a wrong password (record the failed attempt, return generic 401) so the
    # timing/behaviour matches a normal credential failure.
    if not body.password or not body.password.strip():
        return await _fail()

    # Try LDAP first (if enabled)
    ldap_user, _ = await _try_ldap_login(db, body.username, body.password)
    if ldap_user:
        return await _succeed(ldap_user, "ldap")

    # Fall back to local auth
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # Skip local auth for LDAP-only users (no valid local password)
    if user and user.auth_source == "ldap":
        return await _fail("LDAP authentication failed")

    _dummy_hash = b"$2b$12$000000000000000000000uGHEjmFMntPDYjXJPBT3V44YS5gL0nS"
    stored_hash = user.password_hash.encode() if user else _dummy_hash
    pw_ok = verify_password(body.password, stored_hash)
    if not user or not pw_ok:
        return await _fail()

    return await _succeed(user, "local")


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
