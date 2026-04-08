from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
import bcrypt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import User, get_db, is_setup_complete, set_setting

router = APIRouter(prefix="/setup")


@router.get("/status")
async def setup_status(db: AsyncSession = Depends(get_db)):
    return JSONResponse({"setup_complete": await is_setup_complete(db)})


@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if await is_setup_complete(db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse("setup.html", {"request": request})


@router.post("/complete")
async def complete_setup(
    site_name: str = Form("NODEGLOW"),
    username: str = Form("admin"),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if not password.strip():
        return RedirectResponse(url="/setup?error=password_required", status_code=303)
    from utils.password import validate_password
    pw_error = validate_password(password)
    if pw_error:
        from urllib.parse import quote
        return RedirectResponse(url=f"/setup?error={quote(pw_error)}", status_code=303)
    if await is_setup_complete(db):
        return RedirectResponse(url="/", status_code=303)

    # Only create user if none exist
    count = (await db.execute(select(func.count()).select_from(User))).scalar()
    if count == 0:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        db.add(User(username=username.strip() or "admin", password_hash=pw_hash, role="admin"))

    await set_setting(db, "site_name", site_name.strip() or "NODEGLOW")
    await set_setting(db, "ping_interval", "60")
    # Set setup_complete last — only after user creation
    await set_setting(db, "setup_complete", "true")
    await db.commit()

    return RedirectResponse(url="/", status_code=303)
