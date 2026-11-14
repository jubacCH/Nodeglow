"""The sign-in flow — in this version the test sign-in only.

* ``POST /api/identity-providers/{provider_id}/test`` (admin, not through
  support access, licence): starts a flow with purpose ``test``. Answers the
  authorization URL to open and sets the flow cookie.
* ``GET  /api/auth/sso/callback`` (anonymous — it arrives from the provider):
  finds the flow by its state, requires the matching flow cookie, consumes
  the flow once, redeems the code, checks the id token and stores what the
  provider sent, then redirects to ``/login/sso/result``.
* ``GET  /api/identity-providers/tests/{flow_id}`` (the admin who started
  it): the result.

What a callback acts on comes from the flow row alone — tenant, provider,
purpose, nonce, verifier, redirect URI. Nothing in the callback request
(query, headers, host) names a tenant or a provider.

A test creates no account, no identity and no session. Purposes other than
``test`` are refused at the callback while :data:`nodeglow_ee.sso.SIGN_IN_RELEASED`
is False — checked here, at sign-in, not only when a provider is saved.

No transaction is open while a request to the provider is in flight: the
flow is consumed and committed first, the exchange runs, then the rows are
loaded again to write the result.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.identity import IdentityProvider, SsoFlow
from models.settings import User
from ratelimit import rate_limit
from routers.settings._helpers import require_access_admin, require_admin
from services import tenant_keys
from services.audit import log_action
from services.metrics import instrument_job
from services.tenancy import system_scope, tenant_is_active, tenant_scope

from nodeglow_ee import license_runtime, sso
from nodeglow_ee.sso import config, discovery, oidc
from nodeglow_ee.sso.api import _check_inputs, _provider, _public_url, private_issuers_allowed

log = logging.getLogger("nodeglow.ee.sso")

router = APIRouter(tags=["Single sign-on"])

PURPOSE_TEST = "test"
DISCOVERY_MAX_AGE = timedelta(hours=24)
RESULT_PAGE = "/login/sso/result"
RESULT_LIFETIME = timedelta(hours=1)


def _cookie_secure(request: Request) -> bool:
    force = os.environ.get("SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    return force or request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _result_redirect(*, flow_id: int | None = None, error: str | None = None) -> RedirectResponse:
    target = f"{RESULT_PAGE}?flow={flow_id}" if flow_id is not None else f"{RESULT_PAGE}?error={error}"
    response = RedirectResponse(target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.delete_cookie(oidc.FLOW_COOKIE, path=oidc.FLOW_COOKIE_PATH)
    return response


async def _fresh_discovery(db, idp: IdentityProvider, public_only: bool) -> dict:
    """The stored discovery document, refreshed when older than a day. Ends
    the session's transaction before any network call."""
    doc = json.loads(idp.discovery_json) if idp.discovery_json else None
    if doc and idp.discovery_at and datetime.utcnow() - idp.discovery_at < DISCOVERY_MAX_AGE:
        return doc
    inputs = _check_inputs(idp)
    issuer, ca_pem, scopes, groups_claim = inputs
    provider_id = idp.id
    await db.commit()
    try:
        result = await discovery.check(issuer, scopes=scopes, groups_claim=groups_claim,
                                       ca_pem=ca_pem, public_only=public_only)
    except discovery.CheckFailed as exc:
        raise oidc.FlowFailed(exc.code) from None
    fresh = await db.get(IdentityProvider, provider_id, populate_existing=True)
    if fresh is None or _check_inputs(fresh) != inputs:
        raise oidc.FlowFailed("changed")
    fresh.discovery_json = json.dumps(result.discovery, sort_keys=True)
    fresh.discovery_at = datetime.utcnow()
    await db.commit()
    return result.discovery


# ── Start (admin) ────────────────────────────────────────────────────────────

@router.post("/api/identity-providers/{provider_id}/test",
             summary="Start a test sign-in through an identity provider (admin)")
@rate_limit(max_requests=10, window_seconds=60)
async def start_test(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_access_admin(request):
        return err
    idp = await _provider(db, provider_id)
    if idp is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if refused := await license_runtime.blocked(sso.FEATURE):
        return refused
    public_url = await _public_url(db)
    redirect_uri = config.redirect_uri(public_url)
    if not redirect_uri:
        return {"ok": False, "code": "no_public_url",
                "message": "Set the public URL of this installation first."}
    if not idp.client_secret:
        return {"ok": False, "code": "no_client_secret", "message": "Store the client secret first."}
    public_only = not await private_issuers_allowed(db, idp.tenant_id)
    try:
        doc = await _fresh_discovery(db, idp, public_only)
    except oidc.FlowFailed as exc:
        message = (CHANGED if exc.code == "changed" else exc.message)
        return {"ok": False, "code": exc.code, "message": message}
    idp = await db.get(IdentityProvider, provider_id, populate_existing=True)
    if idp is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    secrets_ = oidc.new_flow_secrets()
    user = getattr(request.state, "current_user", None)
    flow = SsoFlow(
        tenant_id=idp.tenant_id, identity_provider_id=idp.id, purpose=PURPOSE_TEST,
        state_hash=oidc.keyed_hash("state", secrets_.state),
        browser_hash=oidc.keyed_hash("browser", secrets_.browser),
        nonce=secrets_.nonce, code_verifier=secrets_.code_verifier,
        user_id=getattr(user, "id", None), redirect_uri=redirect_uri,
        config_hash=idp.config_hash, created_at=datetime.utcnow(),
    )
    db.add(flow)
    await db.flush()
    await log_action(db, request, "idp.test_start", "identity_provider", idp.id, idp.name[:256],
                     details={"flow": flow.id})
    await db.commit()
    url = oidc.authorization_url(doc["authorization_endpoint"], client_id=idp.client_id,
                                 redirect_uri=redirect_uri, scopes=idp.scopes, flow=secrets_)
    response = JSONResponse({"ok": True, "authorization_url": url, "flow": flow.id})
    # Lax, not Strict: the callback is a top-level navigation coming from the
    # provider's site, and a Strict cookie would not travel with it.
    response.set_cookie(oidc.FLOW_COOKIE, secrets_.browser, max_age=oidc.FLOW_LIFETIME,
                        path=oidc.FLOW_COOKIE_PATH, httponly=True, samesite="lax",
                        secure=_cookie_secure(request))
    response.headers["Cache-Control"] = "no-store"
    return response


CHANGED = ("The provider was changed while its discovery document was fetched. "
           "Start the test again.")


# ── Callback (anonymous) ─────────────────────────────────────────────────────

async def _find_flow(db, state: str) -> SsoFlow | None:
    # The state is unique across the installation and names the tenant;
    # looked up before any tenant is known.
    with system_scope("sso: find a sign-in flow by its state"):
        return (await db.execute(
            select(SsoFlow).where(SsoFlow.state_hash == oidc.keyed_hash("state", state))
        )).scalar_one_or_none()


async def _consume(db, flow: SsoFlow) -> bool:
    """Mark the flow used — once. False if it was used already or is too old."""
    oldest = datetime.utcnow() - timedelta(seconds=oidc.FLOW_LIFETIME)
    result = await db.execute(
        update(SsoFlow)
        .where(SsoFlow.id == flow.id, SsoFlow.consumed_at.is_(None), SsoFlow.created_at > oldest)
        .values(consumed_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    return (result.rowcount or 0) == 1


async def _finish(db, request: Request, flow_id: int, provider_id: int, outcome: dict) -> None:
    """Store the outcome for the admin, count a passed test, audit it."""
    flow = await db.get(SsoFlow, flow_id, populate_existing=True)
    idp = await db.get(IdentityProvider, provider_id, populate_existing=True)
    if flow is None:
        return
    if idp is not None and outcome.get("ok"):
        if flow.config_hash and flow.config_hash == idp.config_hash:
            idp.tested_hash = idp.config_hash
            idp.tested_at = datetime.utcnow()
            idp.tested_by = flow.user_id
            outcome["counted"] = True
        else:
            outcome["counted"] = False
            outcome.setdefault("notes", []).append(
                "The provider's settings changed after the test started; it does not count "
                "as a test of the current settings.")
    flow.result_json = json.dumps(outcome, sort_keys=True)
    if flow.user_id is not None:
        request.state.current_user = await db.get(User, flow.user_id)
    await log_action(db, request, "idp.test", "identity_provider", provider_id,
                     (idp.name if idp else "")[:256],
                     details={"ok": bool(outcome.get("ok")), "code": outcome.get("code"),
                              "flow": flow_id})
    await db.commit()


@router.get("/api/auth/sso/callback", summary="Return address for identity providers")
@rate_limit(max_requests=30, window_seconds=60)
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    params = request.query_params
    state = params.get("state") or ""
    if not state or len(state) > 256:
        return _result_redirect(error="invalid_request")
    flow = await _find_flow(db, state)
    browser = request.cookies.get(oidc.FLOW_COOKIE) or ""
    # A flow is completed only in the browser that started it: a callback
    # link planted in someone else's browser does nothing (and does not use
    # the flow up).
    if flow is None or not browser or not hmac.compare_digest(
            oidc.keyed_hash("browser", browser), flow.browser_hash):
        await db.rollback()
        return _result_redirect(error="invalid_request")

    with tenant_scope(flow.tenant_id):
        if not await _consume(db, flow):
            await db.rollback()
            return _result_redirect(error="expired")
        await db.commit()
        flow_id, provider_id, purpose = flow.id, flow.identity_provider_id, flow.purpose
        if purpose != PURPOSE_TEST:
            # Signing in (purpose login / link) is decided here, at sign-in,
            # by the constant — whatever a stored or restored row says. And
            # this version has no sign-in to complete even once it is True.
            code = "not_released" if not sso.SIGN_IN_RELEASED else "not_supported"
            await _finish(db, request, flow_id, provider_id,
                          {"ok": False, "code": code, "message": config.NOT_RELEASED})
            return _result_redirect(error=code)
        if not await tenant_is_active(db, flow.tenant_id):
            await db.rollback()
            return _result_redirect(error="invalid_request")
        outcome = await _run_test(db, request, flow, params)
        await _finish(db, request, flow_id, provider_id, outcome)
    return _result_redirect(flow_id=flow_id)


async def _run_test(db, request: Request, flow: SsoFlow, params) -> dict:
    """The exchange for a test flow. Returns the outcome; ends the
    transaction before talking to the provider."""
    idp = await db.get(IdentityProvider, flow.identity_provider_id, populate_existing=True)
    if idp is None or not license_runtime_active_cached():
        await db.rollback()
        return {"ok": False, "code": "unavailable",
                "message": "The provider or the licence is no longer available."}
    if (error := params.get("error")) is not None:
        await db.rollback()
        code = oidc.oauth_error_code(error)
        return {"ok": False, "code": "provider_error", "message": oidc.MESSAGES["provider_error"],
                "provider_error": code,
                "provider_error_description": oidc.safe_description(params.get("error_description"))}
    iss = params.get("iss")
    if iss is not None and iss != idp.issuer:
        await db.rollback()
        return {"ok": False, "code": "iss_parameter", "message": oidc.MESSAGES["iss_parameter"]}
    code = params.get("code") or ""
    if not code or len(code) > 4096:
        await db.rollback()
        return {"ok": False, "code": "no_code", "message": "The provider sent no authorization code."}
    try:
        doc = json.loads(idp.discovery_json or "null")
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or not doc.get("token_endpoint") or not doc.get("jwks_uri"):
        await db.rollback()
        return {"ok": False, "code": "not_checked", "message": "Check the connection again."}
    try:
        secret = await tenant_keys.decrypt_secret(db, idp.client_secret, tenant_id=idp.tenant_id) \
            if idp.client_secret else None
    except tenant_keys.TenantKeyError:
        secret = None
    if not secret:
        await db.rollback()
        return {"ok": False, "code": "no_client_secret", "message": "Store the client secret first."}
    public_only = not await private_issuers_allowed(db, idp.tenant_id)
    inputs = dict(provider_id=idp.id, doc=doc, issuer=idp.issuer, client_id=idp.client_id,
                  client_secret=secret, code=code, code_verifier=flow.code_verifier,
                  redirect_uri=flow.redirect_uri or "", nonce=flow.nonce,
                  groups_claim=idp.groups_claim, ca_pem=idp.ca_pem, public_only=public_only)
    await db.commit()   # nothing open while the provider is asked
    try:
        result = await oidc.exchange(**inputs)
    except oidc.FlowFailed as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}
    finally:
        inputs.clear()   # the secret and the code go out of scope here
    fresh = await db.get(IdentityProvider, flow.identity_provider_id, populate_existing=True)
    shown = oidc.shown_claims(result.claims, fresh.groups_claim if fresh else "groups")
    return {
        "ok": True, "code": None, "message": "The provider signed the person in and every check passed.",
        "claims": shown, "userinfo_used": result.userinfo_used, "notes": result.notes,
        "preview": config.preview(fresh, shown) if fresh else None,
    }


def license_runtime_active_cached() -> bool:
    from nodeglow_ee.licensing import MODE_OFF

    return license_runtime.manager.cached().feature_mode(sso.FEATURE) != MODE_OFF


# ── Result (the admin who started it) ────────────────────────────────────────

@router.get("/api/identity-providers/tests/{flow_id}", summary="Result of a test sign-in (admin)")
async def test_result(flow_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    flow = await db.get(SsoFlow, flow_id)   # another tenant's flow is None here
    user = getattr(request.state, "current_user", None)
    if flow is None or flow.purpose != PURPOSE_TEST or flow.user_id is None \
            or flow.user_id != getattr(user, "id", None) \
            or flow.created_at < datetime.utcnow() - RESULT_LIFETIME:
        return JSONResponse({"error": "Not found"}, status_code=404)
    idp = await db.get(IdentityProvider, flow.identity_provider_id)
    if flow.result_json is None:
        return {"done": False, "provider": {"id": flow.identity_provider_id,
                                            "name": idp.name if idp else None}}
    return {"done": True, "provider": {"id": flow.identity_provider_id,
                                       "name": idp.name if idp else None},
            "result": json.loads(flow.result_json)}


# ── Cleanup ──────────────────────────────────────────────────────────────────

CLEANUP_JOB_ID = "ee_sso_flow_cleanup"


@instrument_job(CLEANUP_JOB_ID)
async def delete_old_flows(now: datetime | None = None) -> int:
    """Delete flows (and the test results in them) older than an hour."""
    from sqlalchemy import delete

    from models.base import AsyncSessionLocal

    cutoff = (now or datetime.utcnow()) - RESULT_LIFETIME
    async with AsyncSessionLocal() as db:
        with system_scope("sso: delete sign-in flows older than an hour"):
            result = await db.execute(delete(SsoFlow).where(SsoFlow.created_at < cutoff))
            await db.commit()
    return result.rowcount or 0


async def schedule(scheduler) -> None:
    """Scheduler hook."""
    scheduler.add_job(delete_old_flows, "interval", minutes=10, id=CLEANUP_JOB_ID,
                      replace_existing=True, max_instances=1, coalesce=True)
