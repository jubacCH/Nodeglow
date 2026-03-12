import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from templating import templates
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Session, User, get_db
from ratelimit import rate_limit

router = APIRouter()

SESSION_DAYS = 30


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
@rate_limit(max_requests=10, window_seconds=60, html=True)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    # Always run bcrypt to prevent timing-based user enumeration
    _dummy_hash = b"$2b$12$000000000000000000000uGHEjmFMntPDYjXJPBT3V44YS5gL0nS"
    stored_hash = user.password_hash.encode() if user else _dummy_hash
    pw_ok = bcrypt.checkpw(password.encode(), stored_hash)
    if not user or not pw_ok:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Ungültiger Benutzername oder Passwort"},
            status_code=401,
        )
    token = secrets.token_hex(32)
    expires = datetime.utcnow() + timedelta(days=SESSION_DAYS)
    db.add(Session(token=token, user_id=user.id, expires_at=expires))
    await db.commit()
    response = RedirectResponse(url="/", status_code=303)
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie("nodeglow_session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=is_https)
    return response


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("nodeglow_session")
    if token:
        session = await db.get(Session, token)
        if session:
            await db.delete(session)
            await db.commit()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("nodeglow_session")
    return response
