import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Session, User, get_db, get_current_user
from models.settings import _hash_token
from ratelimit import rate_limit
from services.audit import log_action

router = APIRouter()

SESSION_DAYS = 30

# Account lockout: track failed attempts per username
_failed_attempts: dict[str, list[float]] = defaultdict(list)
_LOCKOUT_ATTEMPTS = 5
_LOCKOUT_WINDOW = 900  # 15 minutes


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/login")
@rate_limit(max_requests=10, window_seconds=60)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # Account lockout check
    now = time.monotonic()
    attempts = _failed_attempts[body.username]
    _failed_attempts[body.username] = attempts = [t for t in attempts if t > now - _LOCKOUT_WINDOW]
    if len(attempts) >= _LOCKOUT_ATTEMPTS:
        return JSONResponse({"error": "Account temporarily locked. Try again later."}, status_code=429)

    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    _dummy_hash = b"$2b$12$000000000000000000000uGHEjmFMntPDYjXJPBT3V44YS5gL0nS"
    stored_hash = user.password_hash.encode() if user else _dummy_hash
    pw_ok = bcrypt.checkpw(body.password.encode(), stored_hash)
    if not user or not pw_ok:
        _failed_attempts[body.username].append(now)
        return JSONResponse({"error": "Invalid username or password"}, status_code=401)
    # Clear failed attempts on success
    _failed_attempts.pop(body.username, None)
    token = secrets.token_hex(32)
    token_hash = _hash_token(token)
    expires = datetime.utcnow() + timedelta(days=SESSION_DAYS)
    db.add(Session(token=token_hash, user_id=user.id, expires_at=expires))
    await log_action(db, request, "auth.login", "user", user.id, user.username)
    await db.commit()
    response = JSONResponse({"ok": True, "user": {"id": user.id, "username": user.username, "role": user.role}})
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie("nodeglow_session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=is_https)
    return response


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
        if session:
            await db.delete(session)
            await log_action(db, request, "auth.logout")
            await db.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie("nodeglow_session")
    return response
