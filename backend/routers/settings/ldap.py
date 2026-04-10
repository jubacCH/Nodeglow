"""LDAP authentication settings — save config, test connection."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import encrypt_value, get_db, set_setting
from ratelimit import rate_limit
from services.audit import log_action

from ._helpers import require_admin

router = APIRouter()


@router.post("/ldap/save")
@rate_limit(max_requests=10, window_seconds=60)
async def save_ldap_settings(
    request: Request,
    ldap_enabled:      str = Form("0"),
    ldap_server:       str = Form(""),
    ldap_bind_dn:      str = Form(""),
    ldap_bind_password: str = Form(""),
    ldap_base_dn:      str = Form(""),
    ldap_user_filter:  str = Form(""),
    ldap_display_attr: str = Form("displayName"),
    ldap_group_attr:   str = Form("memberOf"),
    ldap_admin_group:  str = Form(""),
    ldap_editor_group: str = Form(""),
    ldap_use_ssl:      str = Form("0"),
    ldap_start_tls:    str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    """Save LDAP configuration. Admin only."""
    if err := require_admin(request):
        return err

    await set_setting(db, "ldap_enabled", "1" if ldap_enabled in ("1", "true") else "0")
    await set_setting(db, "ldap_server", ldap_server.strip())
    await set_setting(db, "ldap_bind_dn", ldap_bind_dn.strip())
    if ldap_bind_password.strip():
        await set_setting(db, "ldap_bind_password", encrypt_value(ldap_bind_password.strip()))
    await set_setting(db, "ldap_base_dn", ldap_base_dn.strip())
    await set_setting(db, "ldap_user_filter", ldap_user_filter.strip() or
                      "(&(objectClass=person)(sAMAccountName={username}))")
    await set_setting(db, "ldap_display_attr", ldap_display_attr.strip() or "displayName")
    await set_setting(db, "ldap_group_attr", ldap_group_attr.strip() or "memberOf")
    await set_setting(db, "ldap_admin_group", ldap_admin_group.strip())
    await set_setting(db, "ldap_editor_group", ldap_editor_group.strip())
    await set_setting(db, "ldap_use_ssl", "1" if ldap_use_ssl in ("1", "true") else "0")
    await set_setting(db, "ldap_start_tls", "1" if ldap_start_tls in ("1", "true") else "0")
    await db.commit()

    await log_action(db, request, "settings.update", "setting", target_name="ldap")
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/ldap/test")
@rate_limit(max_requests=10, window_seconds=60)
async def test_ldap(request: Request, db: AsyncSession = Depends(get_db)):
    """Test LDAP connection with currently saved settings. Admin only."""
    if err := require_admin(request):
        return err

    from routers.auth import _get_ldap_config
    from services.ldap_auth import test_ldap_connection
    ldap_cfg = await _get_ldap_config(db)
    if not ldap_cfg or not ldap_cfg.server:
        return JSONResponse({"ok": False, "error": "LDAP not configured or not enabled"})

    result = await test_ldap_connection(ldap_cfg)
    return JSONResponse(result)
