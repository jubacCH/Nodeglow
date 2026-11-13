"""Two-factor sign-in: a user's own setup, and the check at login.

Every route here acts on the signed-in user's own account. Turning the
second factor off for somebody else is an admin action in ``routers.users``.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import SECRET_KEY
from database import User, get_db
from models.base import decrypt_value, encrypt_value
from ratelimit import rate_limit
from services import totp
from services.audit import log_action
from services.host_state import iso

# Not under /api/auth/: that prefix skips the session and CSRF checks (it is
# where signing in happens), and these routes need both.
router = APIRouter(prefix="/api/account/mfa", tags=["auth"])


def is_enabled(user) -> bool:
    return bool(getattr(user, "totp_enabled_at", None) and getattr(user, "totp_secret", None))


def status_of(user) -> dict:
    enabled = is_enabled(user)
    return {
        "enabled": enabled,
        "enabled_at": iso(user.totp_enabled_at) if enabled else None,
        "recovery_codes_left": totp.recovery_codes_left(user.totp_recovery) if enabled else 0,
    }


def check_code(user, code: str) -> bool:
    """Accept a current TOTP code or an unused recovery code, and consume it.

    Changes ``user`` (last time step, remaining recovery codes); the caller
    commits. False leaves the user untouched.
    """
    if not is_enabled(user):
        return False
    try:
        secret = decrypt_value(user.totp_secret)
    except Exception:  # noqa: BLE001 - an unreadable secret verifies nothing
        return False
    step = totp.verify(secret, code, last_counter=user.totp_last_counter)
    if step is not None:
        user.totp_last_counter = step
        return True
    remaining = totp.use_recovery_code(user.totp_recovery, code, SECRET_KEY)
    if remaining is not None:
        user.totp_recovery = remaining
        return True
    return False


def clear(user) -> None:
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_counter = None
    user.totp_recovery = None


async def _me(request: Request, db: AsyncSession):
    current = getattr(request.state, "current_user", None)
    if current is None:
        return None
    return await db.get(User, current.id)


def _unauthorized():
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


@router.get("")
async def mfa_status(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _me(request, db)
    if user is None:
        return _unauthorized()
    return status_of(user)


@router.post("/setup")
@rate_limit(max_requests=10, window_seconds=300)
async def mfa_setup(request: Request, db: AsyncSession = Depends(get_db)):
    """Start the setup: a new secret, not active until a code confirms it."""
    user = await _me(request, db)
    if user is None:
        return _unauthorized()
    if is_enabled(user):
        return JSONResponse({"error": "Two-factor sign-in is already on. Turn it off first."},
                            status_code=409)
    secret = totp.new_secret()
    user.totp_secret = encrypt_value(secret)
    user.totp_enabled_at = None
    user.totp_last_counter = None
    user.totp_recovery = None
    await db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.username)}


@router.post("/enable")
@rate_limit(max_requests=10, window_seconds=300)
async def mfa_enable(request: Request, db: AsyncSession = Depends(get_db)):
    """Confirm the setup with a code from the app; returns the recovery codes
    once."""
    user = await _me(request, db)
    if user is None:
        return _unauthorized()
    body = await request.json()
    if is_enabled(user):
        return JSONResponse({"error": "Two-factor sign-in is already on."}, status_code=409)
    if not user.totp_secret:
        return JSONResponse({"error": "Start the setup first."}, status_code=400)
    step = totp.verify(decrypt_value(user.totp_secret), str(body.get("code") or ""))
    if step is None:
        return JSONResponse({"error": "That code is not valid. Check the time on your phone "
                                      "and enter the current code."}, status_code=400)
    codes, stored = totp.new_recovery_codes(SECRET_KEY)
    user.totp_enabled_at = datetime.utcnow()
    user.totp_last_counter = step
    user.totp_recovery = stored
    await log_action(db, request, "user.mfa_enable", "user", user.id, user.username)
    await db.commit()
    return {"ok": True, "recovery_codes": codes}


@router.post("/disable")
@rate_limit(max_requests=10, window_seconds=300)
async def mfa_disable(request: Request, db: AsyncSession = Depends(get_db)):
    """Turn the second factor off. Needs a current code (or a recovery code):
    a session somebody else got hold of must not be enough."""
    user = await _me(request, db)
    if user is None:
        return _unauthorized()
    body = await request.json()
    if not is_enabled(user):
        clear(user)
        await db.commit()
        return {"ok": True}
    if not check_code(user, str(body.get("code") or "")):
        return JSONResponse({"error": "That code is not valid."}, status_code=403)
    clear(user)
    await log_action(db, request, "user.mfa_disable", "user", user.id, user.username)
    await db.commit()
    return {"ok": True}
