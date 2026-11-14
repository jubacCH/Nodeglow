"""The OIDC protocol pieces: flow secrets, PKCE, the authorization request,
the token exchange and the id token checks (spec "The OIDC flow").

Nothing here touches the database; :mod:`.flow` holds the routes and the
flow rows. Every request goes through :func:`.discovery.request_json` (the
guarded client, the address rules of the provider's tenant, no redirects,
size limit) and one exchange — token request, keys, user info — is bounded
by :data:`EXCHANGE_DEADLINE`.

Tokens are used for one validation and then dropped. They, the
authorization code, the code verifier and the client secret are never
logged, stored or put into an error message.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import jwt
from jwt.exceptions import InvalidTokenError, PyJWKError

from nodeglow_ee.sso import discovery
from nodeglow_ee.sso.discovery import ACCEPTED_ALGORITHMS, CheckFailed

FLOW_LIFETIME = 600            # seconds from start to callback
EXCHANGE_DEADLINE = 20.0       # token request, keys and user info together
LEEWAY = 60                    # clock skew allowed on exp / iat
MAX_TOKEN_AGE = 600            # an id token issued longer ago than this is refused
JWKS_TTL = 3600
JWKS_FORCED_REFRESH_EVERY = 60  # at most one refetch per minute for an unknown kid
MAX_GROUPS = 200

FLOW_COOKIE = "ng_sso"
FLOW_COOKIE_PATH = "/api/auth/sso"

_KEY_TYPE = {"RS256": "RSA", "PS256": "RSA", "ES256": "EC"}
_OAUTH_ERROR = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

MESSAGES = {
    "token_error": "The provider refused to exchange the authorization code.",
    "no_id_token": "The provider's token answer has no id token.",
    "unsupported_client_auth": ("The provider accepts neither client_secret_basic nor "
                                "client_secret_post for the token endpoint."),
    "malformed_token": "The id token is not a well-formed signed token.",
    "algorithm": "The id token is signed with an algorithm nodeglow does not accept.",
    "unknown_key": "The id token is signed with a key the provider does not publish.",
    "key_mismatch": "The key named by the id token does not fit its algorithm.",
    "signature": "The id token's signature is not valid.",
    "issuer": "The id token was issued by another issuer.",
    "audience": "The id token is meant for another client.",
    "expired": "The id token has expired.",
    "issued_at": "The id token's issue time is too old or in the future.",
    "nonce": "The id token does not belong to this sign-in (nonce).",
    "subject": "The id token has no usable subject (sub).",
    "provider_error": "The provider ended the sign-in with an error.",
    "iss_parameter": "The callback names another issuer than the provider's.",
}


class FlowFailed(Exception):
    """A sign-in step failed. ``code`` is a fixed category; ``detail`` is
    extra text that is safe to show to the tenant's admin (never a token)."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def message(self) -> str:
        text = MESSAGES.get(self.code) or discovery.MESSAGES.get(self.code) or "The sign-in failed."
        return f"{text} {self.detail}" if self.detail else text


def _from_check(exc: CheckFailed) -> FlowFailed:
    return FlowFailed(exc.code)


# ── Flow secrets ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FlowSecrets:
    state: str
    browser: str
    nonce: str
    code_verifier: str

    @property
    def code_challenge(self) -> str:
        return pkce_challenge(self.code_verifier)


def new_flow_secrets() -> FlowSecrets:
    return FlowSecrets(state=secrets.token_urlsafe(32), browser=secrets.token_urlsafe(32),
                       nonce=secrets.token_urlsafe(24), code_verifier=secrets.token_urlsafe(48))


def keyed_hash(purpose: str, value: str) -> str:
    """HMAC under the installation key: what is stored for the state and the
    flow cookie, so a database read alone cannot complete a flow."""
    from config import SECRET_KEY

    return hmac.new(SECRET_KEY.encode(), f"sso-{purpose}|{value}".encode(), hashlib.sha256).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(endpoint: str, *, client_id: str, redirect_uri: str, scopes: str,
                      flow: FlowSecrets) -> str:
    """The authorization request (code flow, PKCE S256, query response mode)."""
    parts = urlsplit(endpoint)
    query = parse_qsl(parts.query, keep_blank_values=True)
    ours = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": scopes, "state": flow.state, "nonce": flow.nonce,
            "code_challenge": flow.code_challenge, "code_challenge_method": "S256"}
    # The provider's own parameters stay, ours win.
    query = [(k, v) for k, v in query if k not in ours] + list(ours.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def oauth_error_code(value: Any) -> str | None:
    """An OAuth ``error`` value if it has the shape the protocol defines."""
    return value if isinstance(value, str) and _OAUTH_ERROR.match(value) else None


def safe_description(value: Any, limit: int = 200) -> str | None:
    """A provider's ``error_description``: printable, cut to ``limit``."""
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if ch.isprintable())[:limit].strip()
    return text or None


# ── Token exchange ───────────────────────────────────────────────────────────

@dataclass
class Exchange:
    """What a provider sent for one sign-in, after every check."""

    claims: dict
    userinfo_used: bool = False
    notes: list[str] = field(default_factory=list)


def _client_auth(doc: dict, client_id: str, client_secret: str) -> tuple[dict, dict]:
    """``(headers, extra form fields)`` for the token request."""
    methods = doc.get("token_endpoint_auth_methods_supported")
    if not isinstance(methods, list) or "client_secret_basic" in methods:
        raw = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}, {}
    if "client_secret_post" in methods:
        return {}, {"client_id": client_id, "client_secret": client_secret}
    raise FlowFailed("unsupported_client_auth")


# Signing keys per provider: (jwks_uri, keys, fetched at, last forced refresh).
_jwks_cache: dict[int, tuple[str, list, float, float]] = {}


def reset_jwks_cache() -> None:
    _jwks_cache.clear()


async def _keys(client, provider_id: int, jwks_uri: str, public_only: bool, *,
                force: bool = False) -> list:
    now = time.monotonic()
    cached = _jwks_cache.get(provider_id)
    if cached and cached[0] == jwks_uri:
        _uri, keys, fetched, forced = cached
        fresh = now - fetched < JWKS_TTL
        if fresh and not force:
            return keys
        if force and now - forced < JWKS_FORCED_REFRESH_EVERY:
            return keys
    try:
        body = await discovery._get_json(client, jwks_uri, public_only)
    except CheckFailed as exc:
        raise _from_check(exc) from None
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, list):
        raise FlowFailed("jwks_invalid")
    _jwks_cache[provider_id] = (jwks_uri, keys, now, now if force else (cached[3] if cached else 0.0))
    return keys


def _pick_key(keys: list, kid: str | None, alg: str) -> dict | None:
    candidates = [k for k in keys if isinstance(k, dict) and k.get("use") in (None, "sig")]
    if kid is not None:
        found = [k for k in candidates if k.get("kid") == kid]
    else:
        # Without a kid only an unambiguous key of the right type will do.
        found = [k for k in candidates if k.get("kty") == _KEY_TYPE[alg]]
        if len(found) != 1:
            return None
    return found[0] if found else None


async def verify_id_token(client, token: str, *, provider_id: int, issuer: str, client_id: str,
                          nonce: str, jwks_uri: str, public_only: bool,
                          now: float | None = None) -> dict:
    """The id token's claims after every mandatory check, or :class:`FlowFailed`."""
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError:
        raise FlowFailed("malformed_token") from None
    alg = header.get("alg")
    # Before any key is looked at: "none" and every HMAC algorithm are out,
    # so neither the client secret nor a public key can serve as an HMAC key.
    if alg not in ACCEPTED_ALGORITHMS:
        raise FlowFailed("algorithm")
    kid = header.get("kid") if isinstance(header.get("kid"), str) else None
    keys = await _keys(client, provider_id, jwks_uri, public_only)
    jwk = _pick_key(keys, kid, alg)
    if jwk is None and kid is not None:
        keys = await _keys(client, provider_id, jwks_uri, public_only, force=True)
        jwk = _pick_key(keys, kid, alg)
    if jwk is None:
        raise FlowFailed("unknown_key")
    if jwk.get("kty") != _KEY_TYPE[alg] or (alg == "ES256" and jwk.get("crv") != "P-256") \
            or (jwk.get("alg") not in (None, alg)):
        raise FlowFailed("key_mismatch")
    try:
        key = jwt.PyJWK(jwk, algorithm=alg).key
    except (PyJWKError, ValueError, TypeError):
        raise FlowFailed("key_mismatch") from None
    try:
        claims = jwt.decode(
            token, key=key, algorithms=[alg], audience=client_id, issuer=issuer, leeway=LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "sub"], "strict_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise FlowFailed("expired") from None
    except jwt.InvalidIssuerError:
        raise FlowFailed("issuer") from None
    except jwt.InvalidAudienceError:
        raise FlowFailed("audience") from None
    except jwt.InvalidSignatureError:
        raise FlowFailed("signature") from None
    except jwt.ImmatureSignatureError:
        raise FlowFailed("issued_at") from None
    except jwt.MissingRequiredClaimError as exc:
        raise FlowFailed("subject" if exc.claim == "sub" else "malformed_token") from None
    except InvalidTokenError:
        raise FlowFailed("malformed_token") from None
    # Belt and braces on top of the library: exact issuer, audience, azp.
    if claims.get("iss") != issuer:
        raise FlowFailed("issuer")
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
    if client_id not in audiences:
        raise FlowFailed("audience")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise FlowFailed("audience")
    if "azp" in claims and claims.get("azp") != client_id:
        raise FlowFailed("audience")
    current = time.time() if now is None else now
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or iat > current + LEEWAY or iat < current - MAX_TOKEN_AGE - LEEWAY:
        raise FlowFailed("issued_at")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise FlowFailed("nonce")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise FlowFailed("subject")
    return claims


async def exchange(*, provider_id: int, doc: dict, issuer: str, client_id: str,
                   client_secret: str, code: str, code_verifier: str, redirect_uri: str,
                   nonce: str, groups_claim: str, ca_pem: str | None,
                   public_only: bool) -> Exchange:
    """Redeem the code, check the id token, fill missing claims from user
    info — within :data:`EXCHANGE_DEADLINE` for all of it."""
    headers, extra = _client_auth(doc, client_id, client_secret)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": code_verifier, **extra}
    try:
        client = discovery.client_factory(public_only=public_only, ca_pem=ca_pem)
    except Exception:
        raise FlowFailed("tls", "(the CA certificate cannot be used)") from None
    try:
        async with asyncio.timeout(EXCHANGE_DEADLINE):
            async with client:
                try:
                    status, body = await discovery.request_json(
                        client, "POST", doc["token_endpoint"], public_only, data=form,
                        headers={"Accept": "application/json", **headers}, error_json=True)
                except CheckFailed as exc:
                    raise _from_check(exc) from None
                if status != 200:
                    error = oauth_error_code(body.get("error")) if isinstance(body, dict) else None
                    raise FlowFailed("token_error", f"({error})" if error else None)
                token = body.get("id_token") if isinstance(body, dict) else None
                if not isinstance(token, str) or not token:
                    raise FlowFailed("no_id_token")
                claims = await verify_id_token(
                    client, token, provider_id=provider_id, issuer=issuer, client_id=client_id,
                    nonce=nonce, jwks_uri=doc["jwks_uri"], public_only=public_only)
                result = Exchange(claims=claims)
                access = body.get("access_token")
                missing = [c for c in ("email", groups_claim) if c not in claims]
                endpoint = doc.get("userinfo_endpoint")
                if missing and isinstance(access, str) and access and isinstance(endpoint, str):
                    await _merge_userinfo(client, result, endpoint, access, groups_claim, public_only)
    except TimeoutError:
        raise FlowFailed("timeout") from None
    return result


async def _merge_userinfo(client, result: Exchange, endpoint: str, access_token: str,
                          groups_claim: str, public_only: bool) -> None:
    try:
        _status, info = await discovery.request_json(
            client, "GET", endpoint, public_only,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    except CheckFailed as exc:
        result.notes.append(f"User info could not be read ({exc.code}).")
        return
    if not isinstance(info, dict) or info.get("sub") != result.claims.get("sub"):
        # A user info answer about somebody else is ignored entirely.
        result.notes.append("User info was ignored: it names another subject.")
        return
    for claim in ("email", "email_verified", "name", "preferred_username", groups_claim):
        if claim in info and claim not in result.claims:
            result.claims[claim] = info[claim]
    result.userinfo_used = True


# ── What the test sign-in shows ──────────────────────────────────────────────

def groups_of(claims: dict, groups_claim: str) -> list[str]:
    raw = claims.get(groups_claim)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [g for g in raw if isinstance(g, str) and g][:MAX_GROUPS]


def shown_claims(claims: dict, groups_claim: str) -> dict:
    """The claims the tenant's admin sees: identity, mail, name, groups."""
    def text(key: str, limit: int = 320) -> str | None:
        value = claims.get(key)
        return value[:limit] if isinstance(value, str) else None

    aud = claims.get("aud")
    verified = claims.get("email_verified")
    return {
        "sub": text("sub", 255),
        "iss": text("iss", 512),
        "aud": [a for a in ([aud] if isinstance(aud, str) else aud or []) if isinstance(a, str)][:10],
        "email": text("email"),
        "email_verified": verified if isinstance(verified, bool) else
                          (verified == "true" if isinstance(verified, str) else None),
        "name": text("name", 128),
        "preferred_username": text("preferred_username", 255),
        "groups_claim": groups_claim,
        "groups": [g[:255] for g in groups_of(claims, groups_claim)],
        "groups_present": groups_claim in claims,
    }
