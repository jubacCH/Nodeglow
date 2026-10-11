from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import User, get_db, is_setup_complete, set_setting
from models.tenant import DEFAULT_TENANT_ID
from utils.password import hash_password

router = APIRouter(prefix="/setup")


@router.get("/status")
async def setup_status(db: AsyncSession = Depends(get_db)):
    return JSONResponse({"setup_complete": await is_setup_complete(db)})


@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if await is_setup_complete(db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "setup.html")


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

    # The wizard sets up the installation and its first (core: only) tenant.
    tenant_id = DEFAULT_TENANT_ID

    # Only create user if none exist: the installation operator and admin
    # of the first tenant.
    count = (await db.execute(select(func.count()).select_from(User))).scalar()
    if count == 0:
        pw_hash = hash_password(password)
        user = User(username=username.strip() or "admin", password_hash=pw_hash, role="admin",
                    is_operator=True)
        db.add(user)
        await db.flush()
        from services.memberships import grant
        await grant(db, user, tenant_id, "admin")

    from models.tenant import Tenant
    tenant = await db.get(Tenant, tenant_id)
    if tenant is not None:  # site_name is the tenant's display name
        tenant.name = (site_name.strip() or "NODEGLOW")[:128]
    await set_setting(db, "site_name", site_name.strip() or "NODEGLOW", tenant_id=tenant_id)
    await set_setting(db, "ping_interval", "60", tenant_id=tenant_id)
    # Secure-by-default for new installs: only accept syslog from registered hosts.
    # Operators can opt out on the settings page.
    await set_setting(db, "syslog_allowlist_only", "1", tenant_id=tenant_id)
    # Set setup_complete last — only after user creation
    await set_setting(db, "setup_complete", "true")
    await db.commit()

    return RedirectResponse(url="/", status_code=303)
