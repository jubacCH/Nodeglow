"""Single sign-on, the test sign-in (implementation plan PR 2).

A fake issuer in the suite signs real id tokens (RSA and EC keys made per
run) and can misbehave on purpose. Covered: the whole flow (start, provider,
callback, result) with PKCE, state, nonce and client_secret_basic checked by
the fake; every mandatory id token check; the flow consumed once and bound
to the flow cookie; tenant and provider taken from the flow row only; slow
providers cut off; nothing but a test result is created (no user, identity
or session); purposes other than "test" refused at the callback while
sign-in is not released; tokens, the code, the verifier and the client
secret in no log line, audit entry or result.

Runs on SQLite here and on PostgreSQL as ``nodeglow_app`` with RLS on in the
router PostgreSQL mode (``tests/test_migrations_tenancy_pg.py``).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from sqlalchemy import func, select

from models.audit import AuditLog
from models.identity import IdentityProvider, SsoFlow, UserIdentity
from models.settings import Session, User, set_setting
from services.tenancy import system_scope, tenant_scope
from tenancy_helpers import tenancy_env
from utils import net_safety

ISSUER = "https://idp.example.test"
CLIENT_ID = "beta-client"
SECRET = "s3cr3t-client-secret-value"
PUBLIC_URL = "https://nodeglow.example.ch"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeIssuer:
    """Discovery, keys, token and user info of one issuer. Records requests;
    tests change ``claims``, ``token_override``, ``delay`` and friends."""

    def __init__(self, issuer: str = ISSUER):
        self.issuer = issuer
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.jwks = {"keys": [self._jwk(self.rsa.public_key(), "k1", "RSA"),
                              self._jwk(self.ec.public_key(), "e1", "EC")]}
        self.requests: list[httpx.Request] = []
        self.codes: dict[str, dict] = {}     # code -> authorization request params
        self.claims_extra: dict = {}
        self.token_override = None           # callable(params) -> str | None
        self.delay = 0.0
        self.userinfo: dict | None = None
        self.auth_methods: list[str] | None = None

    @staticmethod
    def _jwk(public, kid: str, kty: str) -> dict:
        if kty == "RSA":
            data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public))
        else:
            data = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
        data.update({"kid": kid, "use": "sig"})
        return data

    def document(self) -> dict:
        doc = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize?prompt=login",
            "token_endpoint": f"{self.issuer}/token",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "jwks_uri": f"{self.issuer}/jwks",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256", "ES256"],
            "claims_supported": ["sub", "email", "email_verified", "name", "groups"],
        }
        if self.auth_methods is not None:
            doc["token_endpoint_auth_methods_supported"] = self.auth_methods
        return doc

    def authorize(self, authorization_url: str) -> tuple[str, dict]:
        """What the provider does after the person signs in: a code for this
        request. Returns ``(code, the request's parameters)``."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        code = f"code-{len(self.codes) + 1}-{params['state'][:6]}"
        self.codes[code] = params
        return code, params

    def id_token(self, params: dict, *, alg: str = "RS256", key=None, kid: str | None = "k1",
                 **overrides) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": CLIENT_ID, "sub": "user-123", "iat": now,
                  "exp": now + 300, "nonce": params["nonce"], "email": "jdoe@beta.ch",
                  "email_verified": True, "name": "J. Doe", "groups": ["staff", "nodeglow-admins"],
                  **self.claims_extra}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        headers = {"kid": kid} if kid else {}
        if alg == "ES256":
            return jwt.encode(claims, key or self.ec, algorithm="ES256", headers=headers)
        return jwt.encode(claims, key or self.rsa, algorithm=alg, headers=headers)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if url == f"{self.issuer}/.well-known/openid-configuration":
            return httpx.Response(200, json=self.document())
        if url == f"{self.issuer}/jwks":
            return httpx.Response(200, json=self.jwks)
        if url == f"{self.issuer}/token" and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            params = self.codes.pop(form.get("code", ""), None)   # a code works once
            if params is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            # PKCE, redirect URI and client authentication, as a provider checks them.
            challenge = _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
            if challenge != params["code_challenge"] or form.get("redirect_uri") != params["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            basic = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
            if request.headers.get("authorization") != f"Basic {basic}" and not (
                    form.get("client_id") == CLIENT_ID and form.get("client_secret") == SECRET):
                return httpx.Response(401, json={"error": "invalid_client"})
            token = self.token_override(params) if self.token_override else self.id_token(params)
            body = {"access_token": "access-token-CANARY", "token_type": "Bearer"}
            if token is not None:
                body["id_token"] = token
            return httpx.Response(200, json=body)
        if url == f"{self.issuer}/userinfo":
            return httpx.Response(200, json=self.userinfo or {"sub": "user-123"})
        return httpx.Response(404, text="not here")


@pytest.fixture
def idp_server(monkeypatch):
    from nodeglow_ee.sso import discovery, oidc

    fake = FakeIssuer()
    monkeypatch.setattr(discovery, "client_factory",
                        lambda *, public_only, ca_pem: httpx.AsyncClient(
                            transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(net_safety, "resolve_all",
                        lambda host: [net_safety._parse_ip("93.184.215.14")]
                        if host.endswith("example.test") else [])
    oidc.reset_jwks_cache()
    yield fake
    oidc.reset_jwks_cache()


@pytest.fixture
async def env(idp_server):
    async with tenancy_env() as e:
        await e.tenant(2, "Beta")
        e.a_admin = await e.user("alpha-admin", {1: "admin"}, is_operator=True)
        e.a_session = await e.session(e.a_admin, 1)
        e.b_admin = await e.user("beta-admin", {2: "admin"})
        e.b_session = await e.session(e.b_admin, 2)
        async with e.sf() as s:
            await set_setting(s, "public_url", PUBLIC_URL)
        e.login_as(e.b_session)
        r = await e.client.post("/api/identity-providers", json={
            "name": "Beta", "issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": SECRET,
            "allowed_domains": ["beta.ch"], "role_rules": [{"group": "nodeglow-admins", "role": "admin"}]})
        assert r.status_code == 201, r.text
        e.pid = r.json()["id"]
        yield e


async def _start(env) -> tuple[str, dict, int]:
    r = await env.client.post(f"/api/identity-providers/{env.pid}/test")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    return r.json()["authorization_url"], r.json(), r.json()["flow"]


async def _callback(env, **params) -> httpx.Response:
    return await env.client.get("/api/auth/sso/callback", params=params, follow_redirects=False)


async def _result(env, flow_id: int) -> dict:
    r = await env.client.get(f"/api/identity-providers/tests/{flow_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def _run(env, idp_server) -> tuple[httpx.Response, int, dict]:
    url, _start_body, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    return await _callback(env, code=code, state=params["state"]), flow_id, params


async def _counts(env) -> tuple[int, int, int]:
    async with env.sf() as s:
        with system_scope("test: count", audit=False):
            return tuple([(await s.execute(select(func.count()).select_from(m))).scalar()
                          for m in (User, UserIdentity, Session)])


# ── The whole flow ───────────────────────────────────────────────────────────

async def test_a_test_sign_in_shows_what_the_provider_sent_and_creates_nothing(env, idp_server, caplog):
    # Every application logger at DEBUG, the rest at INFO. (The SQL driver's
    # own DEBUG output echoes bind parameters, the stored verifier included;
    # it is not on in production and asyncpg logs no parameters.)
    caplog.set_level(logging.INFO)
    for name in ("nodeglow", "nodeglow.ee", "nodeglow.ee.sso", "services", "routers", "httpx"):
        caplog.set_level(logging.DEBUG, logger=name)
    before = await _counts(env)
    url, start, flow_id = await _start(env)
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{ISSUER}/authorize?")
    assert query["prompt"] == ["login"]                      # the provider's own parameter stays
    assert query["response_type"] == ["code"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{PUBLIC_URL}/api/auth/sso/callback"]
    assert query["client_id"] == [CLIENT_ID] and "openid" in query["scope"][0]
    assert env.client.cookies.get("ng_sso", path="/api/auth/sso")

    code, params = idp_server.authorize(url)
    r = await _callback(env, code=code, state=params["state"])
    assert r.status_code == 303 and r.headers["location"] == f"/login/sso/result?flow={flow_id}"
    assert r.headers["cache-control"] == "no-store"
    body = await _result(env, flow_id)
    result = body["result"]
    assert body["done"] is True and result["ok"] is True and result["counted"] is True
    claims = result["claims"]
    assert claims["sub"] == "user-123" and claims["email"] == "jdoe@beta.ch"
    assert claims["email_verified"] is True and claims["groups"] == ["staff", "nodeglow-admins"]
    preview = result["preview"]
    assert preview["mail_domain"] == "beta.ch" and preview["domain_allowed"] is True
    assert preview["role"] == "admin" and preview["would_sign_in"] is True

    # Nothing but the result: no user, no identity, no session.
    assert await _counts(env) == before
    # The provider counts as tested with these settings.
    listed = (await env.client.get(f"/api/identity-providers/{env.pid}")).json()
    assert listed["state"] == "tested" and listed["tested_at"]
    # Token request: PKCE verifier, client_secret_basic (checked by the fake).
    token_requests = [q for q in idp_server.requests if str(q.url).endswith("/token")]
    assert len(token_requests) == 1
    # No token, code, verifier or secret anywhere it could be read later.
    async with env.sf() as s:
        with system_scope("test", audit=False):
            flow = await s.get(SsoFlow, flow_id)
            audits = (await s.execute(select(AuditLog.details).where(
                AuditLog.action.like("idp.%")))).scalars().all()
    forbidden = [SECRET, code, flow.code_verifier, "access-token-CANARY", "eyJ"]
    # Log lines as the production handler writes them (with its redaction
    # filter: the callback's request line carries the code and the state).
    from logging_config import RedactSecretsFilter

    redact = RedactSecretsFilter()
    log_text = " | ".join(r.getMessage() for r in caplog.records if redact.filter(r))
    haystacks = {"log": log_text, "audit": " ".join(a or "" for a in audits),
                 "result": flow.result_json, "response": json.dumps(body)}
    for where, text in haystacks.items():
        for needle in forbidden:
            assert needle not in text, (where, needle[:12])


async def test_es256_tokens_are_accepted(env, idp_server):
    idp_server.token_override = lambda p: idp_server.id_token(p, alg="ES256", kid="e1")
    _r, flow_id, _p = await _run(env, idp_server)
    assert (await _result(env, flow_id))["result"]["ok"] is True


async def test_client_secret_post_when_basic_is_not_offered(env, idp_server):
    idp_server.auth_methods = ["client_secret_post"]
    async with env.sf() as s:
        with tenant_scope(2):
            (await s.get(IdentityProvider, env.pid)).discovery_at = None   # refetch the document
            await s.commit()
    _r, flow_id, _p = await _run(env, idp_server)
    assert (await _result(env, flow_id))["result"]["ok"] is True
    token = [q for q in idp_server.requests if str(q.url).endswith("/token")][-1]
    assert "authorization" not in token.headers


# ── Id token checks ──────────────────────────────────────────────────────────

def _hs256_with_public_key(fake: FakeIssuer, params: dict) -> str:
    """The classic key confusion: HMAC "signed" with the public key."""
    pem = fake.rsa.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
    payload = _b64(json.dumps({"iss": fake.issuer, "aud": CLIENT_ID, "sub": "x", "iat": now,
                               "exp": now + 300, "nonce": params["nonce"]}).encode())
    sig = _b64(hmac.new(pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def _alg_none(fake: FakeIssuer, params: dict) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "none"}).encode())
    payload = _b64(json.dumps({"iss": fake.issuer, "aud": CLIENT_ID, "sub": "x", "iat": now,
                               "exp": now + 300, "nonce": params["nonce"]}).encode())
    return f"{header}.{payload}."


def _tampered(fake: FakeIssuer, params: dict) -> str:
    token = fake.id_token(params)
    head, payload, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["sub"] = "somebody-else"
    return f"{head}.{_b64(json.dumps(claims).encode())}.{sig}"


TOKEN_CASES = {
    "issuer": lambda f, p: f.id_token(p, iss="https://evil.example.test"),
    "audience": lambda f, p: f.id_token(p, aud="another-client"),
    "audience_azp": lambda f, p: f.id_token(p, aud=[CLIENT_ID, "other"], azp="other"),
    "expired": lambda f, p: f.id_token(p, exp=int(time.time()) - 120, iat=int(time.time()) - 400),
    "issued_at": lambda f, p: f.id_token(p, iat=int(time.time()) - 3600, exp=int(time.time()) + 300),
    "nonce": lambda f, p: f.id_token(p, nonce="not-this-sign-in"),
    "algorithm_none": _alg_none,
    "algorithm_hs256": _hs256_with_public_key,
    "unknown_key": lambda f, p: f.id_token(p, key=f.other, kid="not-published"),
    "signature": lambda f, p: f.id_token(p, key=f.other, kid="k1"),
    "tampered": _tampered,
    "key_mismatch": lambda f, p: f.id_token(p, kid="e1"),   # RS256 naming the EC key
    "subject": lambda f, p: f.id_token(p, sub=None),
    "no_id_token": lambda f, p: None,
    "malformed": lambda f, p: "not.a.token",
}
EXPECTED = {"algorithm_none": "algorithm", "algorithm_hs256": "algorithm", "audience_azp": "audience",
            "tampered": "signature", "malformed": "malformed_token"}


@pytest.mark.parametrize("case", sorted(TOKEN_CASES))
async def test_every_mandatory_id_token_check(env, idp_server, case):
    idp_server.token_override = lambda p: TOKEN_CASES[case](idp_server, p)
    _r, flow_id, _p = await _run(env, idp_server)
    result = (await _result(env, flow_id))["result"]
    assert result["ok"] is False and result["code"] == EXPECTED.get(case, case), result
    assert "claims" not in result
    listed = (await env.client.get(f"/api/identity-providers/{env.pid}")).json()
    assert listed["state"] == "draft"


async def test_an_unknown_key_refetches_the_keys_once(env, idp_server):
    idp_server.token_override = lambda p: idp_server.id_token(p, key=idp_server.other, kid="rotated")
    await _run(env, idp_server)
    fetches = len([q for q in idp_server.requests if str(q.url).endswith("/jwks")])
    # Key rotation: the provider publishes the new key, the next sign-in works.
    idp_server.jwks["keys"].append(FakeIssuer._jwk(idp_server.other.public_key(), "rotated", "RSA"))
    _r, flow_id, _p = await _run(env, idp_server)
    # Within a minute of the last forced refetch, no new one: still unknown.
    assert (await _result(env, flow_id))["result"]["code"] == "unknown_key"
    assert len([q for q in idp_server.requests if str(q.url).endswith("/jwks")]) == fetches
    from nodeglow_ee.sso import oidc

    oidc.reset_jwks_cache()
    _r, flow_id, _p = await _run(env, idp_server)
    assert (await _result(env, flow_id))["result"]["ok"] is True


# ── State, cookie, replay, tenants ───────────────────────────────────────────

async def test_a_callback_is_used_once(env, idp_server):
    r, flow_id, params = await _run(env, idp_server)
    first = await _result(env, flow_id)
    again = await _callback(env, code="code-again", state=params["state"])
    assert again.headers["location"] == "/login/sso/result?error=invalid_request" or \
        again.headers["location"] == "/login/sso/result?error=expired"
    assert await _result(env, flow_id) == first


async def test_a_callback_without_the_flow_cookie_does_nothing(env, idp_server):
    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    cookie = env.client.cookies.get("ng_sso", path="/api/auth/sso")
    env.client.cookies.delete("ng_sso", path="/api/auth/sso")
    r = await _callback(env, code=code, state=params["state"])
    assert r.headers["location"] == "/login/sso/result?error=invalid_request"
    env.client.cookies.set("ng_sso", "a-cookie-of-another-browser", path="/api/auth/sso")
    r = await _callback(env, code=code, state=params["state"])
    assert r.headers["location"] == "/login/sso/result?error=invalid_request"
    async with env.sf() as s:
        with system_scope("test", audit=False):
            assert (await s.get(SsoFlow, flow_id)).consumed_at is None   # not used up
    # The browser that started it still completes it.
    env.client.cookies.set("ng_sso", cookie, path="/api/auth/sso")
    r = await _callback(env, code=code, state=params["state"])
    assert r.headers["location"] == f"/login/sso/result?flow={flow_id}"


async def test_an_unknown_or_missing_state_redirects_with_an_error(env, idp_server):
    for params in ({}, {"state": "made-up"}, {"state": "x" * 300, "code": "c"}):
        r = await _callback(env, **params)
        assert r.status_code == 303 and r.headers["location"] == "/login/sso/result?error=invalid_request"


async def test_the_flow_row_decides_tenant_and_provider(env, idp_server):
    """Nothing in the callback names a tenant: a tenant header or parameter
    is ignored, the result lands in the flow's tenant, and another tenant's
    admin cannot read it."""
    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    r = await env.client.get("/api/auth/sso/callback", follow_redirects=False,
                             params={"code": code, "state": params["state"], "tenant": "1"},
                             headers={"X-Nodeglow-Tenant": "1"})
    assert r.headers["location"] == f"/login/sso/result?flow={flow_id}"
    async with env.sf() as s:
        with system_scope("test", audit=False):
            flow = await s.get(SsoFlow, flow_id)
            [audit] = (await s.execute(select(AuditLog).where(AuditLog.action == "idp.test"))).scalars().all()
    assert flow.tenant_id == 2 and audit.tenant_id == 2 and audit.user_id == env.b_admin
    env.login_as(env.a_session)
    assert (await env.client.get(f"/api/identity-providers/tests/{flow_id}")).status_code == 404
    assert (await env.client.get(f"/api/identity-providers/tests/{flow_id}",
                                 headers={"X-Nodeglow-Tenant": "2"})).status_code == 404


async def test_the_state_of_another_tenants_flow_needs_that_browser(env, idp_server):
    # B's admin starts a test; A's admin, with a flow cookie of their own,
    # tries to complete B's flow with its state.
    url_b, _s, flow_b = await _start(env)
    code_b, params_b = idp_server.authorize(url_b)
    env.client.cookies.delete("ng_sso", path="/api/auth/sso")
    env.login_as(env.a_session)
    r = await env.client.post("/api/identity-providers", json={
        "name": "Alpha", "issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": SECRET,
        "login_key": "alpha-idp"})
    a_pid = r.json()["id"]
    r = await env.client.post(f"/api/identity-providers/{a_pid}/test")
    assert r.json()["ok"] is True
    r = await _callback(env, code=code_b, state=params_b["state"])
    assert r.headers["location"] == "/login/sso/result?error=invalid_request"
    async with env.sf() as s:
        with system_scope("test", audit=False):
            assert (await s.get(SsoFlow, flow_b)).consumed_at is None


async def test_only_the_admin_who_started_it_sees_the_result(env, idp_server):
    _r, flow_id, _p = await _run(env, idp_server)
    other = await env.user("beta-admin-2", {2: "admin"})
    env.login_as(await env.session(other, 2))
    assert (await env.client.get(f"/api/identity-providers/tests/{flow_id}")).status_code == 404


async def test_an_old_flow_is_expired(env, idp_server):
    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    async with env.sf() as s:
        with tenant_scope(2):
            (await s.get(SsoFlow, flow_id)).created_at = datetime.utcnow() - timedelta(minutes=11)
            await s.commit()
    r = await _callback(env, code=code, state=params["state"])
    assert r.headers["location"] == "/login/sso/result?error=expired"


async def test_sign_in_purposes_are_refused_while_not_released(env, idp_server):
    """A login flow — restored, hand-made, or from a later version — is
    refused at the callback; no session, no user."""
    from nodeglow_ee.sso import oidc

    before = await _counts(env)
    secrets_ = oidc.new_flow_secrets()
    async with env.sf() as s:
        with tenant_scope(2):
            s.add(SsoFlow(tenant_id=2, identity_provider_id=env.pid, purpose="login",
                          state_hash=oidc.keyed_hash("state", secrets_.state),
                          browser_hash=oidc.keyed_hash("browser", secrets_.browser),
                          nonce=secrets_.nonce, code_verifier=secrets_.code_verifier,
                          redirect_uri=f"{PUBLIC_URL}/api/auth/sso/callback"))
            await s.commit()
    env.client.cookies.set("ng_sso", secrets_.browser, path="/api/auth/sso")
    r = await _callback(env, code="whatever", state=secrets_.state)
    assert r.headers["location"] == "/login/sso/result?error=not_released"
    assert await _counts(env) == before
    assert not [q for q in idp_server.requests if str(q.url).endswith("/token")]


# ── Provider answers ─────────────────────────────────────────────────────────

async def test_a_provider_error_is_shown_cut_and_printable(env, idp_server):
    url, _s, flow_id = await _start(env)
    _code, params = idp_server.authorize(url)
    await _callback(env, state=params["state"], error="access_denied",
                    error_description="User\x00 cancelled " + "x" * 400)
    result = (await _result(env, flow_id))["result"]
    assert result["code"] == "provider_error" and result["provider_error"] == "access_denied"
    assert "\x00" not in result["provider_error_description"]
    assert len(result["provider_error_description"]) <= 200


async def test_a_refused_code_and_a_foreign_iss_parameter(env, idp_server):
    url, _s, flow_id = await _start(env)
    _code, params = idp_server.authorize(url)
    await _callback(env, state=params["state"], code="never-issued")
    result = (await _result(env, flow_id))["result"]
    assert result["code"] == "token_error" and "invalid_grant" in result["message"]

    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    await _callback(env, state=params["state"], code=code, iss="https://evil.example.test")
    assert (await _result(env, flow_id))["result"]["code"] == "iss_parameter"


async def test_a_slow_provider_is_cut_off_at_the_deadline(env, idp_server, monkeypatch):
    from nodeglow_ee.sso import oidc

    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    monkeypatch.setattr(oidc, "EXCHANGE_DEADLINE", 0.5)
    idp_server.delay = 2.0
    started = time.monotonic()
    await _callback(env, code=code, state=params["state"])
    assert time.monotonic() - started < 2.0
    assert (await _result(env, flow_id))["result"]["code"] == "timeout"


async def test_no_transaction_is_open_while_the_provider_is_asked(env, idp_server):
    import gc

    from sqlalchemy.orm import Session as OrmSession

    seen = []
    real = idp_server.handler

    async def watching(request):
        if str(request.url).endswith("/token"):
            seen.append(sum(1 for o in gc.get_objects()
                            if isinstance(o, OrmSession) and o.in_transaction()))
        return await real(request)

    idp_server.handler = watching
    await _run(env, idp_server)
    assert seen == [0]


async def test_user_info_fills_missing_claims_and_is_ignored_for_another_subject(env, idp_server):
    idp_server.claims_extra = {"email": None, "groups": None, "email_verified": None}
    idp_server.userinfo = {"sub": "user-123", "email": "jdoe@beta.ch", "email_verified": True,
                           "groups": ["staff"]}
    _r, flow_id, _p = await _run(env, idp_server)
    result = (await _result(env, flow_id))["result"]
    assert result["userinfo_used"] is True and result["claims"]["email"] == "jdoe@beta.ch"
    idp_server.userinfo = {"sub": "somebody-else", "email": "boss@beta.ch"}
    _r, flow_id, _p = await _run(env, idp_server)
    result = (await _result(env, flow_id))["result"]
    assert result["claims"]["email"] is None and any("another subject" in n for n in result["notes"])


async def test_the_preview_follows_the_settings(env, idp_server):
    idp_server.claims_extra = {"email_verified": False, "groups": ["staff"]}
    _r, flow_id, _p = await _run(env, idp_server)
    preview = (await _result(env, flow_id))["result"]["preview"]
    assert preview["domain_allowed"] is False and preview["would_sign_in"] is False
    assert preview["role"] == "readonly" and preview["role_from"] == "default"
    assert any("not marked as verified" in r for r in preview["reasons"])


async def test_a_flow_of_changed_settings_sends_nothing(env, idp_server):
    """Settings changed after the start: the callback refuses before any
    request, so nothing reaches the token endpoint of the old or new issuer."""
    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    r = await env.client.patch(f"/api/identity-providers/{env.pid}", json={"scopes": "openid email"})
    assert r.status_code == 200
    await _callback(env, code=code, state=params["state"])
    result = (await _result(env, flow_id))["result"]
    assert result["ok"] is False and result["code"] == "changed"
    assert not [q for q in idp_server.requests if str(q.url).endswith("/token")]
    assert (await env.client.get(f"/api/identity-providers/{env.pid}")).json()["state"] == "draft"


# ── Start and cleanup ────────────────────────────────────────────────────────

async def test_start_needs_a_public_url_a_secret_and_the_right_person(env, idp_server):
    async with env.sf() as s:
        await set_setting(s, "public_url", "")
    r = await env.client.post(f"/api/identity-providers/{env.pid}/test")
    assert r.json()["code"] == "no_public_url"
    async with env.sf() as s:
        await set_setting(s, "public_url", PUBLIC_URL)
    await env.client.patch(f"/api/identity-providers/{env.pid}", json={"clear_client_secret": True})
    assert (await env.client.post(f"/api/identity-providers/{env.pid}/test")).json()["code"] == \
        "no_client_secret"
    editor = await env.user("beta-editor", {2: "editor"})
    env.login_as(await env.session(editor, 2))
    assert (await env.client.post(f"/api/identity-providers/{env.pid}/test")).status_code == 403
    env.login_as(env.a_session)
    assert (await env.client.post(f"/api/identity-providers/{env.pid}/test")).status_code == 404


async def test_old_flows_and_their_results_are_deleted(env, idp_server):
    from nodeglow_ee.sso import flow as flow_mod

    _r, flow_id, _p = await _run(env, idp_server)
    assert await flow_mod.delete_old_flows(now=datetime.utcnow() + timedelta(minutes=30)) == 0
    assert await flow_mod.delete_old_flows(now=datetime.utcnow() + timedelta(hours=2)) >= 1
    async with env.sf() as s:
        with system_scope("test", audit=False):
            assert await s.get(SsoFlow, flow_id) is None


# ── Fixes after the security review of PR 57 ─────────────────────────────────

async def _secret_set(env) -> bool:
    return (await env.client.get(f"/api/identity-providers/{env.pid}")).json()["client_secret_set"]


@pytest.mark.parametrize("change", [
    {"issuer": "https://attacker.example.test"},
    {"client_id": "another-client"},
])
async def test_changing_where_the_secret_goes_clears_it(env, idp_server, change):
    r = await env.client.patch(f"/api/identity-providers/{env.pid}", json=change)
    assert r.status_code == 200 and r.json()["client_secret_cleared"] is True
    assert r.json()["client_secret_set"] is False
    [audit] = [a for a in await env.audit("idp.update")]
    assert json.loads(audit.details)["client_secret_cleared"] is True
    # Nothing to send any more: the test cannot start, no request goes out.
    started = (await env.client.post(f"/api/identity-providers/{env.pid}/test")).json()
    assert started["code"] == "no_client_secret"
    assert not [q for q in idp_server.requests if str(q.url).endswith("/token")]


async def test_a_new_ca_certificate_clears_the_secret_too(env, idp_server):
    from datetime import timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    # The class cryptography checks against, looked up now: the clock-shift
    # CI job replaces datetime.datetime after this module imported its own
    # reference. The dates do not matter here.
    import datetime as dt

    start = dt.datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = dt.datetime(2100, 1, 1, tzinfo=timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(start).not_valid_after(end)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    r = await env.client.patch(f"/api/identity-providers/{env.pid}", json={"ca_pem": pem})
    assert r.json()["client_secret_cleared"] is True and not await _secret_set(env)


async def test_a_new_secret_with_the_change_and_other_edits_keep_it(env, idp_server):
    r = await env.client.patch(f"/api/identity-providers/{env.pid}",
                               json={"issuer": "https://new.example.test", "client_secret": "fresh"})
    assert r.json()["client_secret_cleared"] is False and await _secret_set(env)
    r = await env.client.patch(f"/api/identity-providers/{env.pid}",
                               json={"name": "Renamed", "scopes": "openid email", "session_hours": 4})
    assert r.json()["client_secret_cleared"] is False and await _secret_set(env)


async def test_the_issuer_switch_attack_gets_no_secret(env, idp_server):
    """The scenario from the review: point the issuer at your own server,
    run a test, point it back. The stored secret is gone with the first
    step, and a flow started before the switch sends nothing afterwards."""
    url, _s, flow_id = await _start(env)
    code, params = idp_server.authorize(url)
    await env.client.patch(f"/api/identity-providers/{env.pid}",
                           json={"issuer": "https://attacker.example.test"})
    await _callback(env, code=code, state=params["state"])
    assert (await _result(env, flow_id))["result"]["code"] == "changed"
    assert [q for q in idp_server.requests if str(q.url).endswith("/token")] == []
    assert not await _secret_set(env)


async def test_a_malformed_key_under_the_tokens_kid_is_a_result_not_a_500(env, idp_server):
    from nodeglow_ee.sso import oidc

    idp_server.jwks = {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "n": "%%%", "e": "AQAB"}]}
    oidc.reset_jwks_cache()
    r, flow_id, _p = await _run(env, idp_server)
    assert r.status_code == 303
    assert (await _result(env, flow_id))["result"]["code"] == "key_mismatch"


async def test_a_non_ascii_nonce_is_a_nonce_mismatch(env, idp_server):
    idp_server.token_override = lambda p: idp_server.id_token(p, nonce="nönce-✓")
    r, flow_id, _p = await _run(env, idp_server)
    assert r.status_code == 303
    assert (await _result(env, flow_id))["result"]["code"] == "nonce"


async def test_an_unexpected_error_still_records_a_result(env, idp_server, monkeypatch, caplog):
    from nodeglow_ee.sso import oidc

    async def boom(*a, **kw):
        raise RuntimeError("detail that may carry token parts eyJ-SECRET")

    monkeypatch.setattr(oidc, "verify_id_token", boom)
    r, flow_id, _p = await _run(env, idp_server)
    assert r.status_code == 303
    assert (await _result(env, flow_id))["result"]["code"] == "unexpected_answer"
    assert "eyJ-SECRET" not in caplog.text


async def test_a_test_needs_an_https_public_url(env, idp_server):
    async with env.sf() as s:
        await set_setting(s, "public_url", "http://nodeglow.example.ch")
    r = (await env.client.post(f"/api/identity-providers/{env.pid}/test")).json()
    assert r["code"] == "no_https_public_url"
    listed = (await env.client.get(f"/api/identity-providers/{env.pid}")).json()
    assert any("not an https" in b for b in listed["blockers"])


async def test_cached_keys_are_dropped_when_the_provider_changes(env, idp_server):
    from nodeglow_ee.sso import oidc

    await _run(env, idp_server)
    assert env.pid in oidc._jwks_cache
    await env.client.patch(f"/api/identity-providers/{env.pid}", json={"name": "Renamed"})
    assert env.pid not in oidc._jwks_cache


async def test_an_idle_cleanup_writes_no_audit_entry(env, idp_server):
    from nodeglow_ee.sso import flow as flow_mod

    before = len(await env.audit("system_scope.write"))
    assert await flow_mod.delete_old_flows() == 0
    assert len(await env.audit("system_scope.write")) == before
