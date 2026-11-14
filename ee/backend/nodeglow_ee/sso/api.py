"""HTTP routes of single sign-on: the administration of a tenant's providers.

The request's tenant (filtered like every tenant route; another tenant's
provider is "not found"):

* ``GET    /api/identity-providers`` — the providers, the redirect address
  to register at the provider, what the licence allows (admin);
* ``POST   /api/identity-providers`` — create (admin, not through support
  access: who may sign in is access management);
* ``GET    /api/identity-providers/{id}`` — one provider (admin);
* ``PATCH  /api/identity-providers/{id}`` — change (as create);
* ``DELETE /api/identity-providers/{id}`` — delete, with its identities and
  sessions (as create; also after the licence expired);
* ``POST   /api/identity-providers/{id}/check`` — check connection (as create).

The installation operator:

* ``PUT    /api/identity-providers/installation`` — ``{"private_issuers":
  bool}``: whether providers of other tenants than the default one may use
  private addresses (owner decision 7, default off).

The client secret is never returned; it is stored under the tenant's data
key. There is no sign-in route yet (:data:`nodeglow_ee.sso.SIGN_IN_RELEASED`).
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.identity import IdentityProvider
from models.settings import get_setting, set_setting
from models.tenant import DEFAULT_TENANT_ID, Tenant
from ratelimit import rate_limit
from routers.settings._helpers import require_access_admin, require_admin, require_operator
from services import tenant_keys
from services.audit import log_action
from services.tenancy import request_via_support, session_tenant_id, system_scope

from nodeglow_ee import license_runtime, sso
from nodeglow_ee.licensing import MODE_FULL, MODE_OFF
from nodeglow_ee.sso import cleanup, config, discovery

router = APIRouter(tags=["Single sign-on"])

PRIVATE_ISSUERS_KEY = "sso_private_issuers"
MAX_PROVIDERS = 20
_KEY_ATTEMPTS = 5


async def _body(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _error(message: str, status: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


async def _mode() -> str:
    return (await license_runtime.manager.current()).feature_mode(sso.FEATURE)


async def _public_url(db) -> str:
    return ((await get_setting(db, "public_url", "")) or "").strip().rstrip("/")


async def private_issuers_allowed(db, tenant_id: int) -> bool:
    """Providers of the default tenant are the operator's own and may sit in
    the LAN; any other tenant's only where the operator allows it."""
    if tenant_id == DEFAULT_TENANT_ID:
        return True
    return (await get_setting(db, PRIVATE_ISSUERS_KEY, "0")) == "1"


async def _login_key_taken(db, key: str, *, except_id: int | None = None) -> bool:
    # Keys are unique across the installation; other tenants' rows are
    # invisible in this tenant's scope.
    with system_scope("sso: is a sign-in key taken"):
        found = (await db.execute(
            select(IdentityProvider.id).where(IdentityProvider.login_key == key)
        )).scalars().all()
    return any(i != except_id for i in found)


async def _provider(db, provider_id: int) -> IdentityProvider | None:
    # The tenant filter makes another tenant's provider None here.
    return await db.get(IdentityProvider, provider_id)


async def _store_secret(db, idp: IdentityProvider, secret: str | None) -> None:
    if secret is None:
        return
    idp.client_secret = (await tenant_keys.encrypt_secret(db, secret, tenant_id=idp.tenant_id)
                         if secret else None)


async def _secret_plain(db, idp: IdentityProvider) -> str | None:
    if not idp.client_secret:
        return None
    return await tenant_keys.decrypt_secret(db, idp.client_secret, tenant_id=idp.tenant_id)


async def _refresh_hash(db, idp: IdentityProvider) -> None:
    idp.config_hash = config.config_hash(idp, await _secret_plain(db, idp))


@router.get("/api/identity-providers", summary="Identity providers of this tenant (admin)")
async def list_providers(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    tenant_id = session_tenant_id(db)
    public_url = await _public_url(db)
    providers = (await db.execute(
        select(IdentityProvider).order_by(IdentityProvider.id)
    )).scalars().all()
    mode = await _mode()
    from services.tenancy import request_is_operator

    return {
        "providers": [config.public(p, public_url=public_url) for p in providers],
        "redirect_uri": config.redirect_uri(public_url),
        "public_url_set": bool(public_url),
        "licence": mode,
        "can_manage": mode == MODE_FULL and not request_via_support(request),
        "can_delete": mode != MODE_OFF and not request_via_support(request),
        "sign_in_released": sso.SIGN_IN_RELEASED,
        "limits": {"max_providers": MAX_PROVIDERS, "session_hours": [
            config.MIN_SESSION_HOURS, config.MAX_SESSION_HOURS]},
        "private_issuers": {
            "allowed_here": await private_issuers_allowed(db, tenant_id),
            "default_tenant": tenant_id == DEFAULT_TENANT_ID,
            "setting": (await get_setting(db, PRIVATE_ISSUERS_KEY, "0")) == "1",
            "can_change": request_is_operator(request) and not request_via_support(request),
        },
    }


@router.post("/api/identity-providers", summary="Add an identity provider (admin)")
@rate_limit(max_requests=20, window_seconds=60)
async def create_provider(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_access_admin(request):
        return err
    if refused := await license_runtime.blocked(sso.FEATURE):
        return refused
    try:
        changes = config.validate(await _body(request), creating=True)
    except config.ConfigError as exc:
        return _error(exc.message, field=exc.field)
    count = len((await db.execute(select(IdentityProvider.id))).scalars().all())
    if count >= MAX_PROVIDERS:
        return _error(f"A tenant can have up to {MAX_PROVIDERS} identity providers.", 409)

    tenant_id = session_tenant_id(db)
    values = dict(changes.values)
    if "login_key" in values:
        if await _login_key_taken(db, values["login_key"]):
            return _error("This sign-in key is taken. Choose another one.", 409, field="login_key")
    else:
        tenant = await db.get(Tenant, tenant_id)
        for _ in range(_KEY_ATTEMPTS):
            candidate = config.new_login_key(tenant.slug if tenant else None)
            if not await _login_key_taken(db, candidate):
                values["login_key"] = candidate
                break
        else:  # pragma: no cover - 29 bits of randomness
            return _error("Could not pick a sign-in key. Try again.", 503)

    user = getattr(request.state, "current_user", None)
    now = datetime.utcnow()
    idp = IdentityProvider(
        tenant_id=tenant_id, type="oidc", enabled=False, scopes=config.DEFAULT_SCOPES,
        groups_claim="groups", jit_enabled=True, allowed_domains="[]", required_groups="[]",
        role_rules="[]", default_role="readonly", issuer_is_own_organisation=False,
        session_hours=12, sso_required=False, created_at=now, updated_at=now,
        created_by=getattr(user, "id", None),
    )
    for key, value in values.items():
        setattr(idp, key, value)
    db.add(idp)
    await db.flush()
    await _store_secret(db, idp, changes.client_secret)
    await _refresh_hash(db, idp)
    await log_action(db, request, "idp.create", "identity_provider", idp.id, idp.name[:256],
                     details={"issuer": idp.issuer, "login_key": idp.login_key,
                              "client_secret_set": bool(idp.client_secret)})
    await db.commit()
    return JSONResponse(config.public(idp, public_url=await _public_url(db)), status_code=201)


@router.put("/api/identity-providers/installation",
            summary="Allow private issuer addresses for every tenant (operator)")
async def set_installation(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_operator(request):
        return err
    if request_via_support(request):
        return _error("Support access cannot change installation settings.", 403)
    if refused := await license_runtime.blocked(sso.FEATURE):
        return refused
    body = await _body(request)
    if not isinstance(body, dict) or not isinstance(body.get("private_issuers"), bool):
        return _error("Send {\"private_issuers\": true|false}.")
    value = "1" if body["private_issuers"] else "0"
    previous = await get_setting(db, PRIVATE_ISSUERS_KEY, "0")
    if value != previous:
        await set_setting(db, PRIVATE_ISSUERS_KEY, value, commit=False)
        # Installation level: it applies to every tenant.
        with system_scope("audit: installation setting for identity providers"):
            await log_action(db, request, "sso.private_issuers", "setting", None,
                             PRIVATE_ISSUERS_KEY, details={"from": previous == "1",
                                                           "to": value == "1"})
            await db.commit()
    return {"private_issuers": value == "1"}


@router.get("/api/identity-providers/{provider_id}", summary="One identity provider (admin)")
async def get_provider(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    idp = await _provider(db, provider_id)
    if idp is None:
        return _error("Not found", 404)
    data = config.public(idp, public_url=await _public_url(db))
    data["affected_users"] = await cleanup.affected_users(db, idp)
    return data


@router.patch("/api/identity-providers/{provider_id}", summary="Change an identity provider (admin)")
@rate_limit(max_requests=30, window_seconds=60)
async def update_provider(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_access_admin(request):
        return err
    idp = await _provider(db, provider_id)
    if idp is None:
        return _error("Not found", 404)
    if refused := await license_runtime.blocked(sso.FEATURE):
        return refused
    try:
        changes = config.validate(await _body(request), creating=False)
    except config.ConfigError as exc:
        return _error(exc.message, field=exc.field)
    values = changes.values
    if "login_key" in values and values["login_key"] != idp.login_key \
            and await _login_key_taken(db, values["login_key"], except_id=idp.id):
        return _error("This sign-in key is taken. Choose another one.", 409, field="login_key")
    changed = sorted(k for k, v in values.items() if getattr(idp, k) != v)
    for key in changed:
        setattr(idp, key, values[key])
    if changes.client_secret is not None:
        changed.append("client_secret")
        await _store_secret(db, idp, changes.client_secret)
    if "issuer" in changed or "ca_pem" in changed:
        # The stored document belongs to the old issuer.
        idp.discovery_json = None
        idp.discovery_at = None
    if changed:
        idp.updated_at = datetime.utcnow()
        await _refresh_hash(db, idp)
        await log_action(db, request, "idp.update", "identity_provider", idp.id, idp.name[:256],
                         details={"changed": changed})
        await db.commit()
    return config.public(idp, public_url=await _public_url(db))


@router.delete("/api/identity-providers/{provider_id}", summary="Delete an identity provider (admin)")
async def delete_provider(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    # Taking access away is allowed whatever the licence says, as long as the
    # feature was licensed once (an expired licence).
    if err := require_access_admin(request):
        return err
    idp = await _provider(db, provider_id)
    if idp is None:
        return _error("Not found", 404)
    if await _mode() == MODE_OFF:
        if refused := await license_runtime.blocked(sso.FEATURE):
            return refused
    name, issuer, pid = idp.name, idp.issuer, idp.id
    removed = await cleanup.delete_provider(db, idp)
    await log_action(db, request, "idp.delete", "identity_provider", pid, name[:256],
                     details={"issuer": issuer, **removed.__dict__})
    await db.commit()
    return {"ok": True, **removed.__dict__}


@router.post("/api/identity-providers/{provider_id}/check",
             summary="Check the connection to an identity provider (admin)")
@rate_limit(max_requests=10, window_seconds=60)
async def check_provider(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_access_admin(request):
        return err
    idp = await _provider(db, provider_id)
    if idp is None:
        return _error("Not found", 404)
    if refused := await license_runtime.blocked(sso.FEATURE):
        return refused
    public_only = not await private_issuers_allowed(db, idp.tenant_id)
    try:
        result = await discovery.check(idp.issuer, scopes=idp.scopes,
                                       groups_claim=idp.groups_claim, ca_pem=idp.ca_pem,
                                       public_only=public_only)
    except discovery.CheckFailed as exc:
        await log_action(db, request, "idp.check", "identity_provider", idp.id, idp.name[:256],
                         details={"ok": False, "code": exc.code})
        await db.commit()
        return {"ok": False, "code": exc.code, "message": exc.message}
    idp.discovery_json = json.dumps(result.discovery, sort_keys=True)
    idp.discovery_at = datetime.utcnow()
    await log_action(db, request, "idp.check", "identity_provider", idp.id, idp.name[:256],
                     details={"ok": True, "algorithms": result.algorithms,
                              "signing_keys": result.signing_keys})
    await db.commit()
    return {
        "ok": True,
        "algorithms": result.algorithms,
        "signing_keys": result.signing_keys,
        "notes": result.notes,
        "provider": config.public(idp, public_url=await _public_url(db)),
    }
