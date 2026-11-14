"""Single sign-on, administration (implementation plan PR 1): a tenant admin
creates, edits, checks and deletes the tenant's OIDC providers.

Covered: the client secret is stored under the tenant's data key and never
returned; another tenant's providers are invisible and untouchable; support
access cannot change who may sign in; validation; check connection against a
fake issuer (no network), including the outbound address rules; the licence
states; what deleting a provider removes; the operator's installation
setting; providers cannot be enabled while sign-in is not released.

Runs on SQLite here and on PostgreSQL as ``nodeglow_app`` with RLS on in the
router PostgreSQL mode (``tests/test_migrations_tenancy_pg.py``).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from models.identity import IdentityProvider, SsoFlow, UserIdentity
from models.settings import Session, User
from models.tenant import ProviderMembership, SupportGrant, TenantMembership
from services import tenant_keys
from services.tenancy import system_scope, tenant_scope
from tenancy_helpers import tenancy_env
from utils import net_safety

from ee_license_helpers import issue_test_license

ISSUER = "https://idp.example.test"
SECRET = "s3cr3t-client-secret-value"
B_HEADER = {"X-Nodeglow-Tenant": "2"}


def discovery_doc(issuer: str = ISSUER, **overrides) -> dict:
    doc = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "userinfo_endpoint": f"{issuer}/userinfo",
        "jwks_uri": f"{issuer}/jwks",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "claims_supported": ["sub", "email", "email_verified", "name", "groups"],
    }
    doc.update(overrides)
    return {k: v for k, v in doc.items() if v is not None}


JWKS = {"keys": [{"kty": "RSA", "use": "sig", "kid": "k1", "n": "sXch", "e": "AQAB"}]}


class FakeIssuer:
    """Serves discovery and keys per host; records what was requested."""

    def __init__(self):
        self.routes: dict[str, httpx.Response] = {}
        self.requests: list[str] = []
        self.serve(ISSUER)

    def serve(self, issuer: str, doc: dict | None = None, jwks: dict | None = None) -> None:
        doc = doc or discovery_doc(issuer)
        self.routes[f"{issuer}/.well-known/openid-configuration"] = httpx.Response(200, json=doc)
        self.routes[doc.get("jwks_uri") or f"{issuer}/jwks"] = httpx.Response(200, json=jwks or JWKS)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        return self.routes.get(url, httpx.Response(404, text="CANARY-internal-404-body"))


@pytest.fixture
def issuer(monkeypatch):
    from nodeglow_ee.sso import discovery

    fake = FakeIssuer()

    def factory(*, public_only, ca_pem):
        fake.last_public_only = public_only
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(discovery, "client_factory", factory)
    # No DNS in tests: names resolve to a public documentation-free address,
    # a few to private ones.
    answers = {"idp.example.test": "93.184.215.14", "lan-idp.example.test": "192.168.1.20"}
    monkeypatch.setattr(net_safety, "resolve_all", lambda host: [
        net_safety._parse_ip(answers[host])] if host in answers else [])
    return fake


@pytest.fixture
async def env():
    async with tenancy_env() as e:
        await e.tenant(2, "Beta")
        e.a_admin = await e.user("alpha-admin", {1: "admin"}, is_operator=True)
        e.a_session = await e.session(e.a_admin, 1)
        e.b_admin = await e.user("beta-admin", {2: "admin"})
        e.b_session = await e.session(e.b_admin, 2)
        e.b_editor = await e.user("beta-editor", {2: "editor"})
        e.b_editor_session = await e.session(e.b_editor, 2)
        yield e


async def _create(env, **body):
    payload = {"name": "Beta (Infomaniak)", "issuer": ISSUER, "client_id": "beta-client",
               "client_secret": SECRET, **body}
    return await env.client.post("/api/identity-providers", json=payload)


async def _row(env, provider_id: int) -> IdentityProvider | None:
    async with env.sf() as s:
        with system_scope("test: read a provider", audit=False):
            return await s.get(IdentityProvider, provider_id)


# ── Create, read, secret handling ────────────────────────────────────────────

async def test_admin_creates_a_provider_and_the_secret_stays_in_the_tenant_key(env):
    env.login_as(env.b_session)
    r = await _create(env)
    assert r.status_code == 201, r.text
    data = r.json()
    assert SECRET not in r.text and data["client_secret_set"] is True
    assert data["state"] == "draft" and data["enabled"] is False
    assert data["login_key"].startswith("t2-") and len(data["login_key"]) == len("t2-") + 6
    assert data["scopes"] == "openid profile email" and data["default_role"] == "readonly"
    assert any("not available in this version" in b for b in data["blockers"])

    row = await _row(env, data["id"])
    assert row.tenant_id == 2 and row.client_secret.startswith(tenant_keys.FORMAT_PREFIX)
    async with env.sf() as s:
        with tenant_scope(2):
            assert await tenant_keys.decrypt_secret(s, row.client_secret, tenant_id=2) == SECRET
        with tenant_scope(1):
            # Tenant 1's key cannot open it.
            with pytest.raises(tenant_keys.TenantKeyError):
                await tenant_keys.decrypt_secret(s, row.client_secret, tenant_id=1)
    [audit] = await env.audit("idp.create")
    assert audit.tenant_id == 2 and SECRET not in (audit.details or "")

    listed = (await env.client.get("/api/identity-providers")).json()
    assert [p["id"] for p in listed["providers"]] == [data["id"]]
    assert SECRET not in json.dumps(listed)
    assert listed["licence"] == "full" and listed["can_manage"] is True
    assert listed["sign_in_released"] is False
    detail = await env.client.get(f"/api/identity-providers/{data['id']}")
    assert detail.status_code == 200 and SECRET not in detail.text
    assert detail.json()["affected_users"] == 0


async def test_redirect_and_sign_in_addresses_come_from_the_public_url_only(env):
    env.login_as(env.a_session)
    r = await _create(env, login_key="alpha-sso")
    assert r.json()["sign_in_url"] is None
    listed = (await env.client.get("/api/identity-providers",
                                   headers={"X-Forwarded-Host": "evil.example"})).json()
    assert listed["redirect_uri"] is None and listed["public_url_set"] is False
    assert any("public address" in b for b in listed["providers"][0]["blockers"])
    async with env.sf() as s:
        from models.settings import set_setting

        await set_setting(s, "public_url", "https://nodeglow.example.ch")
    listed = (await env.client.get("/api/identity-providers",
                                   headers={"X-Forwarded-Host": "evil.example"})).json()
    assert listed["redirect_uri"] == "https://nodeglow.example.ch/api/auth/sso/callback"
    assert listed["providers"][0]["sign_in_url"] == "https://nodeglow.example.ch/login/sso/alpha-sso"


async def test_update_keeps_replaces_and_clears_the_secret(env):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    first = await _row(env, pid)

    r = await env.client.patch(f"/api/identity-providers/{pid}",
                               json={"name": "Renamed", "client_secret": "   "})
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert (await _row(env, pid)).client_secret == first.client_secret  # blank keeps it

    r = await env.client.patch(f"/api/identity-providers/{pid}", json={"client_secret": "new-one"})
    assert r.status_code == 200 and "new-one" not in r.text
    second = await _row(env, pid)
    assert second.client_secret != first.client_secret and second.config_hash != first.config_hash
    updates = await env.audit("idp.update")
    assert "client_secret" in json.loads(updates[-1].details)["changed"]
    assert "new-one" not in updates[-1].details

    r = await env.client.patch(f"/api/identity-providers/{pid}", json={"clear_client_secret": True})
    assert r.json()["client_secret_set"] is False and (await _row(env, pid)).client_secret is None
    r = await env.client.patch(f"/api/identity-providers/{pid}",
                               json={"clear_client_secret": True, "client_secret": "x"})
    assert r.status_code == 400


async def test_rotating_the_tenant_key_re_encrypts_the_client_secret(env):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    before = (await _row(env, pid)).client_secret
    async with env.sf() as s:
        with system_scope("test: rotate", audit=False):
            report = await tenant_keys.rotate_tenant_key(s, 2)
            await s.commit()
    after = (await _row(env, pid)).client_secret
    assert report.converted >= 1 and after != before
    async with env.sf() as s:
        with tenant_scope(2):
            assert await tenant_keys.decrypt_secret(s, after, tenant_id=2) == SECRET


# ── Isolation and roles ──────────────────────────────────────────────────────

async def test_another_tenants_provider_is_not_found_and_stays_unchanged(env, issuer):
    env.login_as(env.b_session)
    pid = (await _create(env, name="CANARY-B-idp")).json()["id"]
    before = await _row(env, pid)

    env.login_as(env.a_session)
    listed = await env.client.get("/api/identity-providers")
    assert listed.status_code == 200 and "CANARY-B" not in listed.text
    assert (await env.client.get(f"/api/identity-providers/{pid}")).status_code == 404
    assert (await env.client.patch(f"/api/identity-providers/{pid}",
                                   json={"name": "taken over"})).status_code == 404
    assert (await env.client.post(f"/api/identity-providers/{pid}/check")).status_code == 404
    assert (await env.client.delete(f"/api/identity-providers/{pid}")).status_code == 404
    # Asking for tenant 2 explicitly: A's admin is no member there.
    assert (await env.client.get("/api/identity-providers", headers=B_HEADER)).status_code == 404
    after = await _row(env, pid)
    assert (after.name, after.client_secret, after.updated_at) == \
        (before.name, before.client_secret, before.updated_at)
    assert issuer.requests == []


async def test_a_sign_in_key_is_unique_across_tenants(env):
    env.login_as(env.a_session)
    assert (await _create(env, login_key="shared-key")).status_code == 201
    env.login_as(env.b_session)
    r = await _create(env, login_key="shared-key")
    assert r.status_code == 409 and r.json()["field"] == "login_key"
    pid = (await _create(env, login_key="beta-key")).json()["id"]
    r = await env.client.patch(f"/api/identity-providers/{pid}", json={"login_key": "shared-key"})
    assert r.status_code == 409
    # Its own key again is no conflict.
    r = await env.client.patch(f"/api/identity-providers/{pid}", json={"login_key": "beta-key"})
    assert r.status_code == 200


async def test_editors_and_readers_cannot_see_or_change_providers(env):
    env.login_as(env.b_editor_session)
    assert (await env.client.get("/api/identity-providers")).status_code == 403
    assert (await _create(env)).status_code == 403


async def test_support_access_may_look_but_not_change_who_signs_in(env):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    async with env.sf() as s:
        with system_scope("test: grant", audit=False):
            s.add(SupportGrant(tenant_id=2, role="admin", reason="ticket", created_at=datetime.utcnow(),
                               expires_at=datetime.utcnow() + timedelta(hours=2), granted_by=env.b_admin))
            await s.commit()
    from nodeglow_ee.tenancy import support

    support.invalidate_grants()
    env.login_as(env.a_session)
    listed = await env.client.get("/api/identity-providers", headers=B_HEADER)
    assert listed.status_code == 200 and listed.json()["can_manage"] is False
    for call in (
        env.client.post("/api/identity-providers", headers=B_HEADER,
                        json={"name": "x", "issuer": ISSUER, "client_id": "c"}),
        env.client.patch(f"/api/identity-providers/{pid}", headers=B_HEADER, json={"name": "x"}),
        env.client.delete(f"/api/identity-providers/{pid}", headers=B_HEADER),
        env.client.post(f"/api/identity-providers/{pid}/check", headers=B_HEADER),
    ):
        r = await call
        assert r.status_code == 403 and r.json().get("code") == "support_access", r.text
    assert (await _row(env, pid)).name == "Beta (Infomaniak)"


# ── Validation ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,field", [
    ({"issuer": "http://idp.example.test"}, "issuer"),
    ({"issuer": "https://idp.example.test/?tenant=x"}, "issuer"),
    ({"issuer": "https://user:pw@idp.example.test"}, "issuer"),
    ({"issuer": "not a url"}, "issuer"),
    ({"scopes": "profile email"}, "scopes"),
    ({"scopes": 'openid "x"'}, "scopes"),
    ({"client_id": "has blank"}, "client_id"),
    ({"default_role": "admin"}, "default_role"),
    ({"session_hours": 0}, "session_hours"),
    ({"session_hours": 169}, "session_hours"),
    ({"session_hours": True}, "session_hours"),
    ({"allowed_domains": ["not a domain"]}, "allowed_domains"),
    ({"allowed_domains": "acme.ch"}, "allowed_domains"),
    ({"role_rules": [{"group": "g", "role": "owner"}]}, "role_rules"),
    ({"role_rules": [{"group": "", "role": "admin"}]}, "role_rules"),
    ({"required_groups": [""]}, "required_groups"),
    ({"groups_claim": "has blank"}, "groups_claim"),
    ({"login_key": "UPPER_and_underscore"}, "login_key"),
    ({"login_key": "ab"}, "login_key"),
    ({"ca_pem": "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"}, "ca_pem"),
    ({"jit_enabled": "yes"}, "jit_enabled"),
    ({"tenant_id": 1}, "tenant_id"),
    ({"enabled": True}, "enabled"),
    ({"sso_required": True}, "sso_required"),
])
async def test_invalid_settings_are_refused(env, body, field):
    env.login_as(env.b_session)
    r = await _create(env, **body)
    assert r.status_code == 400, r.text
    assert r.json()["field"] == field
    async with env.sf() as s:
        with system_scope("test", audit=False):
            assert (await s.execute(select(IdentityProvider))).scalars().all() == []


async def test_lists_are_normalised(env):
    env.login_as(env.b_session)
    r = await _create(env, scopes="openid  email openid groups",
                      allowed_domains=["Beta.CH", "@beta.ch", "sub.beta.ch."],
                      required_groups=[" staff ", "staff"],
                      role_rules=[{"group": "admins", "role": "admin"}, {"group": "admins", "role": "admin"}],
                      default_role="none", session_hours=8)
    data = r.json()
    assert data["scopes"] == "openid email groups"
    assert data["allowed_domains"] == ["beta.ch", "sub.beta.ch"]
    assert data["required_groups"] == ["staff"]
    assert data["role_rules"] == [{"group": "admins", "role": "admin"}]
    assert (data["default_role"], data["session_hours"]) == ("none", 8)


async def test_an_open_provider_needs_a_restriction_or_the_confirmation(env):
    env.login_as(env.b_session)
    data = (await _create(env)).json()
    assert any("everyone the issuer signs in" in b for b in data["blockers"])
    for body in ({"allowed_domains": ["beta.ch"]}, {"required_groups": ["staff"]},
                 {"jit_enabled": False}, {"issuer_is_own_organisation": True}):
        fresh = (await _create(env, **body)).json()
        assert not any("everyone the issuer signs in" in b for b in fresh["blockers"]), body


# ── Check connection ─────────────────────────────────────────────────────────

async def test_check_connection_stores_the_checked_document(env, issuer):
    env.login_as(env.b_session)
    pid = (await _create(env, allowed_domains=["beta.ch"])).json()["id"]
    r = await env.client.post(f"/api/identity-providers/{pid}/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["algorithms"] == ["RS256"] and body["signing_keys"] == 1
    assert body["provider"]["discovery"]["token_endpoint"] == f"{ISSUER}/token"
    assert body["provider"]["discovery_at"]
    assert issuer.last_public_only is True   # tenant 2, private addresses not allowed
    [audit] = await env.audit("idp.check")
    assert audit.tenant_id == 2 and json.loads(audit.details)["ok"] is True
    # Changing the issuer drops the document of the old one.
    r = await env.client.patch(f"/api/identity-providers/{pid}",
                               json={"issuer": "https://other.example.test"})
    assert r.json()["discovery"] is None and r.json()["discovery_at"] is None


@pytest.mark.parametrize("change,code", [
    (dict(doc=discovery_doc("https://idp.example.test/")), "issuer_mismatch"),
    (dict(doc=discovery_doc(token_endpoint="http://idp.example.test/token")), "insecure_endpoint"),
    (dict(doc=discovery_doc(id_token_signing_alg_values_supported=["HS256", "none"])),
     "no_supported_algorithm"),
    (dict(doc=discovery_doc(code_challenge_methods_supported=["plain"])), "no_pkce"),
    (dict(doc=discovery_doc(response_types_supported=["id_token"])), "no_code_flow"),
    (dict(doc=discovery_doc(jwks_uri=None)), "not_discovery"),
    (dict(jwks={"keys": [{"kty": "oct", "k": "c2VjcmV0"}]}), "jwks_invalid"),
    (dict(jwks={"keys": [{"kty": "RSA", "use": "enc", "n": "x", "e": "AQAB"}]}), "jwks_invalid"),
])
async def test_check_connection_names_what_is_wrong(env, issuer, change, code):
    issuer.serve(ISSUER, **change)
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["ok"] is False and body["code"] == code, body
    assert (await _row(env, pid)).discovery_json is None


async def test_check_connection_never_shows_the_targets_answer(env, issuer):
    issuer.routes.clear()
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    r = await env.client.post(f"/api/identity-providers/{pid}/check")
    body = r.json()
    assert body["code"] == "http_status" and "CANARY-internal" not in r.text
    issuer.routes[f"{ISSUER}/.well-known/openid-configuration"] = httpx.Response(
        302, headers={"Location": "http://169.254.169.254/"})
    assert (await env.client.post(f"/api/identity-providers/{pid}/check")).json()["code"] == "redirect"
    issuer.routes[f"{ISSUER}/.well-known/openid-configuration"] = httpx.Response(
        200, text="CANARY-internal <html>")
    r = await env.client.post(f"/api/identity-providers/{pid}/check")
    assert r.json()["code"] == "not_json" and "CANARY-internal" not in r.text
    from nodeglow_ee.sso import discovery

    issuer.routes[f"{ISSUER}/.well-known/openid-configuration"] = httpx.Response(
        200, content=b" " * (discovery.MAX_BYTES + 10))
    assert (await env.client.post(f"/api/identity-providers/{pid}/check")).json()["code"] == "too_large"


async def test_private_issuers_only_for_the_default_tenant_unless_the_operator_allows(env, issuer):
    lan = "https://lan-idp.example.test"
    issuer.serve(lan)
    env.login_as(env.b_session)
    pid = (await _create(env, issuer=lan)).json()["id"]
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["code"] == "private_address" and issuer.requests == []
    # A literal private address, and a blocked one, likewise.
    lit = (await _create(env, issuer="https://10.0.0.5")).json()["id"]
    assert (await env.client.post(f"/api/identity-providers/{lit}/check")).json()["code"] == \
        "private_address"
    meta = (await _create(env, issuer="https://169.254.169.254")).json()["id"]
    assert (await env.client.post(f"/api/identity-providers/{meta}/check")).json()["code"] == \
        "blocked_address"

    # The tenant admin cannot allow it; the operator can.
    r = await env.client.put("/api/identity-providers/installation", json={"private_issuers": True})
    assert r.status_code == 403
    env.login_as(env.a_session)
    r = await env.client.put("/api/identity-providers/installation", json={"private_issuers": True})
    assert r.status_code == 200 and r.json() == {"private_issuers": True}
    [audit] = await env.audit("sso.private_issuers")
    assert audit.tenant_id is None
    env.login_as(env.b_session)
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["ok"] is True and issuer.last_public_only is False
    # Blocked addresses stay blocked with the setting on.
    assert (await env.client.post(f"/api/identity-providers/{meta}/check")).json()["code"] == \
        "blocked_address"


async def test_the_default_tenant_may_use_its_own_lan(env, issuer):
    lan = "https://lan-idp.example.test"
    issuer.serve(lan)
    env.login_as(env.a_session)
    pid = (await _create(env, issuer=lan)).json()["id"]
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["ok"] is True and issuer.last_public_only is False
    listed = (await env.client.get("/api/identity-providers")).json()
    assert listed["private_issuers"]["allowed_here"] is True
    assert listed["private_issuers"]["can_change"] is True


# ── Delete ───────────────────────────────────────────────────────────────────

async def test_deleting_a_provider_removes_identities_sessions_and_users_of_it_only(env):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    async with env.sf() as s:
        sso_user = User(username="jdoe@beta.ch#%d" % pid, password_hash="x", role="readonly",
                        auth_source="oidc")
        shared = User(username="shared@beta.ch#%d" % pid, password_hash="x", role="readonly",
                      auth_source="oidc")
        s.add_all([sso_user, shared])
        await s.flush()
        s.add_all([TenantMembership(user_id=sso_user.id, tenant_id=2, role="readonly"),
                   TenantMembership(user_id=shared.id, tenant_id=2, role="readonly"),
                   TenantMembership(user_id=shared.id, tenant_id=1, role="readonly"),
                   ProviderMembership(user_id=shared.id, provider_id=1, role="msp_readonly")])
        with tenant_scope(2):
            s.add_all([
                UserIdentity(tenant_id=2, identity_provider_id=pid, user_id=sso_user.id,
                             external_subject="sub-1", created_via="jit"),
                UserIdentity(tenant_id=2, identity_provider_id=pid, user_id=shared.id,
                             external_subject="sub-2", created_via="jit"),
                UserIdentity(tenant_id=2, identity_provider_id=pid, user_id=env.b_admin,
                             external_subject="sub-3", created_via="link"),
                SsoFlow(tenant_id=2, identity_provider_id=pid, state_hash="st", purpose="login",
                        browser_hash="b", nonce="n", code_verifier="v"),
            ])
            await s.flush()
        s.add(Session(token="sso-session", user_id=sso_user.id, active_tenant_id=2,
                      identity_provider_id=pid, expires_at=datetime.utcnow() + timedelta(hours=1)))
        await s.commit()
        sso_id, shared_id = sso_user.id, shared.id

    assert (await env.client.get(f"/api/identity-providers/{pid}")).json()["affected_users"] == 3
    r = await env.client.delete(f"/api/identity-providers/{pid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "identities": 3, "sessions": 1, "memberships_revoked": 2,
                        "users_deleted": 1}
    async with env.sf() as s:
        assert await s.get(User, sso_id) is None                  # existed only through it
        assert await s.get(User, shared_id) is not None            # also elsewhere: kept
        assert await s.get(User, env.b_admin) is not None          # a linked local account
        assert await s.get(Session, "sso-session") is None
        with system_scope("test", audit=False):
            for model in (IdentityProvider, UserIdentity, SsoFlow):
                assert (await s.execute(select(model))).scalars().all() == [], model
            members = (await s.execute(select(TenantMembership.tenant_id).where(
                TenantMembership.user_id == shared_id))).scalars().all()
    assert members == [1]
    [audit] = await env.audit("idp.delete")
    assert audit.tenant_id == 2 and json.loads(audit.details)["users_deleted"] == 1


# ── Licence ──────────────────────────────────────────────────────────────────

async def test_without_a_licence_nothing_can_be_changed(env, ee_license):
    ee_license.clear()
    env.login_as(env.b_session)
    assert (await _create(env)).status_code == 402
    listed = (await env.client.get("/api/identity-providers")).json()
    assert listed["licence"] == "off" and listed["can_manage"] is False
    feats = (await env.client.get("/api/v2/features")).json()
    assert feats["installed"]["sso"] is True and feats["features"]["sso"] is False


async def test_an_expired_licence_keeps_providers_readable_and_deletable(env, ee_license):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    ee_license.set(issue_test_license(days=-30))  # past the grace period
    listed = (await env.client.get("/api/identity-providers")).json()
    assert listed["licence"] == "read_only" and [p["id"] for p in listed["providers"]] == [pid]
    assert listed["can_manage"] is False and listed["can_delete"] is True
    assert (await env.client.patch(f"/api/identity-providers/{pid}",
                                   json={"name": "x"})).status_code == 402
    assert (await env.client.post(f"/api/identity-providers/{pid}/check")).status_code == 402
    assert (await env.client.delete(f"/api/identity-providers/{pid}")).status_code == 200


async def test_a_licence_without_sso_does_not_unlock_it(env, ee_license):
    ee_license.set(issue_test_license(features=("multi_tenancy",)))
    env.login_as(env.b_session)
    r = await _create(env)
    assert r.status_code == 402 and r.json()["code"] == "feature_not_licensed"


# ── Fixes after the review of PR 56 ──────────────────────────────────────────

async def test_a_slow_drip_answer_is_cut_off_at_the_overall_deadline(env, issuer, monkeypatch):
    """Five seconds per read do not bound a server that sends a byte every
    few seconds; the overall deadline does."""
    import asyncio
    import time

    from nodeglow_ee.sso import discovery

    monkeypatch.setattr(discovery, "DEADLINE", 0.6)

    async def drip():
        yield b"{"
        for _ in range(100):
            await asyncio.sleep(0.1)
            yield b" "

    issuer.routes[f"{ISSUER}/.well-known/openid-configuration"] = httpx.Response(200, content=drip())
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    started = time.monotonic()
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["code"] == "timeout" and time.monotonic() - started < 3


async def test_no_database_transaction_is_held_while_the_check_waits(env, issuer, monkeypatch):
    import gc

    from sqlalchemy.orm import Session as OrmSession

    from nodeglow_ee.sso import discovery

    seen: list[int] = []
    real = discovery.check

    async def watching(*args, **kwargs):
        seen.append(sum(1 for o in gc.get_objects()
                        if isinstance(o, OrmSession) and o.in_transaction()))
        return await real(*args, **kwargs)

    monkeypatch.setattr(discovery, "check", watching)
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body["ok"] is True and seen == [0]


async def test_a_check_result_is_not_stored_when_the_provider_changed_meanwhile(env, issuer, monkeypatch):
    from nodeglow_ee.sso import discovery

    real = discovery.check
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]

    async def racing(*args, **kwargs):
        result = await real(*args, **kwargs)
        async with env.sf() as s:
            with tenant_scope(2):
                row = await s.get(IdentityProvider, pid)
                row.issuer = "https://new-issuer.example.test"
                await s.commit()
        return result

    monkeypatch.setattr(discovery, "check", racing)
    body = (await env.client.post(f"/api/identity-providers/{pid}/check")).json()
    assert body == {"ok": False, "code": "changed", "message": body["message"]}
    row = await _row(env, pid)
    assert row.discovery_json is None and row.issuer == "https://new-issuer.example.test"


async def test_check_failures_carry_no_content_of_the_target(env, issuer):
    issuer.serve(ISSUER, doc=discovery_doc("https://CANARY-target.example.test"))
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    r = await env.client.post(f"/api/identity-providers/{pid}/check")
    assert r.json()["code"] == "issuer_mismatch" and "CANARY" not in r.text
    issuer.routes[f"{ISSUER}/.well-known/openid-configuration"] = httpx.Response(418, text="x")
    r = await env.client.post(f"/api/identity-providers/{pid}/check")
    assert r.json()["code"] == "http_status" and "418" not in r.text


async def _link_oidc_admin(env, pid: int, *, admin_of: int = 2) -> int:
    async with env.sf() as s:
        user = User(username=f"boss@beta.ch#{pid}", password_hash="x", role="admin", auth_source="oidc")
        s.add(user)
        await s.flush()
        s.add(TenantMembership(user_id=user.id, tenant_id=admin_of, role="admin"))
        with tenant_scope(2):
            s.add(UserIdentity(tenant_id=2, identity_provider_id=pid, user_id=user.id,
                               external_subject=f"boss-{pid}", created_via="jit"))
            await s.commit()
        return user.id


async def test_deleting_the_provider_you_sign_in_through_is_refused(env):
    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    boss = await _link_oidc_admin(env, pid)
    env.login_as(await env.session(boss, 2))
    r = await env.client.delete(f"/api/identity-providers/{pid}")
    assert r.status_code == 409 and "your own access" in r.json()["error"]
    assert await _row(env, pid) is not None
    async with env.sf() as s:
        assert await s.get(User, boss) is not None


async def test_a_delete_that_would_leave_the_tenant_without_an_admin_is_refused(env):
    from nodeglow_ee.sso import cleanup

    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    await _link_oidc_admin(env, pid)
    async with env.sf() as s:
        await s.execute(TenantMembership.__table__.update()
                        .where(TenantMembership.user_id == env.b_admin).values(role="editor"))
        await s.commit()
    async with env.sf() as s:
        with tenant_scope(2):
            idp = await s.get(IdentityProvider, pid)
            with pytest.raises(cleanup.DeleteRefused, match="without an admin"):
                await cleanup.delete_provider(s, idp, acting_user_id=None)
            await s.rollback()
    assert await _row(env, pid) is not None


async def test_a_restore_cannot_bring_back_an_enabled_provider(env):
    """A backup of a later version (or an edited one) carries enabled = true;
    while sign-in is not released it is switched off, and never reported as
    enabled meanwhile."""
    from unittest.mock import AsyncMock, patch

    from services import backup

    env.login_as(env.b_session)
    pid = (await _create(env)).json()["id"]
    async with env.sf() as s:
        with system_scope("test: as a restore would leave it", audit=False):
            await s.execute(IdentityProvider.__table__.update().values(enabled=True, sso_required=True))
            await s.commit()
    data = (await env.client.get(f"/api/identity-providers/{pid}")).json()
    assert data["state"] == "draft" and data["enabled"] is False

    async with env.sf() as s:
        with tenant_scope(1), patch.object(backup, "_restore_tables", new=AsyncMock(return_value={})):
            await backup.import_backup(s, {"_meta": {"format": "nodeglow-backup"}, "tables": {}})
    row = await _row(env, pid)
    assert (row.enabled, row.sso_required) == (False, False)


def test_sso_registers_its_restore_hook():
    import extensions
    from nodeglow_ee import sso

    assert sso.after_restore in extensions.registry.restore_hooks
