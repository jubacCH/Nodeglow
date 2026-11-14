"""Check connection: fetch and check a provider's discovery document and keys.

Everything goes through ``utils.net_safety``: the address is checked before
the request and again at connect time, redirects are not followed, TLS is
always verified (against a private CA if the provider has one) and a
response is read up to :data:`MAX_BYTES`. :data:`TIMEOUT` bounds each
network operation (connect, each read); :data:`DEADLINE` bounds the whole
check, both requests included, so a server that sends a byte every few
seconds cannot keep it alive. With ``public_only`` (a provider of any other
tenant than the default one, unless the operator allows private addresses)
every address that is not globally routable is refused as well.

Failures are reported as a category with a fixed message. Nothing of the
target's answer is shown or logged, neither bodies nor field values nor the
HTTP status: the check must not become a way to read internal services.
What remains is inherent to any connection test: the category itself
(cannot connect, timeout, TLS, an error answer) tells whether something
listens at an address the tenant may check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from utils import net_safety

log = logging.getLogger("nodeglow.ee.sso")

DISCOVERY_PATH = "/.well-known/openid-configuration"
# Signature algorithms nodeglow accepts for id tokens (spec "Id token checks").
ACCEPTED_ALGORITHMS = ("RS256", "PS256", "ES256")
MAX_BYTES = 1_000_000
TIMEOUT = 5.0     # per network operation
DEADLINE = 15.0   # the whole check

# Kept from the discovery document; everything else is dropped.
KEPT_FIELDS = (
    "issuer", "authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri",
    "end_session_endpoint", "id_token_signing_alg_values_supported", "scopes_supported",
    "claims_supported", "code_challenge_methods_supported", "response_types_supported",
    "token_endpoint_auth_methods_supported",
)

MESSAGES = {
    "blocked_address": "The address is not allowed for an identity provider.",
    "private_address": ("The provider resolves to a private address. Providers of this tenant "
                        "must be reachable on a public address, unless the operator allows "
                        "private ones."),
    "cannot_connect": "Cannot connect to the provider.",
    "timeout": "The provider did not answer in time.",
    "tls": "The provider's TLS certificate is not trusted.",
    "http_status": "The provider answered with an error instead of the document.",
    "redirect": "The provider answered with a redirect; enter the issuer exactly as the provider states it.",
    "too_large": "The provider's answer is too large.",
    "not_json": "The provider's answer is not JSON.",
    "not_discovery": "This is not an OpenID Connect discovery document.",
    "issuer_mismatch": ("The discovery document states a different issuer. Enter the issuer "
                        "exactly as the provider documents it (a trailing slash counts)."),
    "insecure_endpoint": "An endpoint of the provider is not an https:// address.",
    "no_code_flow": "The provider does not offer the authorization code flow.",
    "no_pkce": "The provider does not support PKCE with S256.",
    "no_supported_algorithm": ("The provider signs id tokens only with algorithms nodeglow does "
                               "not accept (accepted: RS256, PS256, ES256)."),
    "jwks_invalid": "The provider's signing keys cannot be used.",
}


class CheckFailed(Exception):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def message(self) -> str:
        text = MESSAGES.get(self.code, "The check failed.")
        return f"{text} {self.detail}" if self.detail else text


@dataclass
class CheckResult:
    discovery: dict
    algorithms: list[str]
    signing_keys: int
    notes: list[str] = field(default_factory=list)


def _ssl_context(ca_pem: str | None) -> ssl.SSLContext | bool:
    if not ca_pem:
        return True
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=ca_pem)
    return ctx


def _default_client(*, public_only: bool, ca_pem: str | None) -> httpx.AsyncClient:
    return net_safety.safe_async_client(
        public_only=public_only, verify=_ssl_context(ca_pem), timeout=TIMEOUT,
        follow_redirects=False, trust_env=False,
        headers={"Accept": "application/json", "User-Agent": "nodeglow-sso-check"},
    )


# Replaced by the tests with a client on a fake issuer.
client_factory: Callable[..., httpx.AsyncClient] = _default_client


async def _precheck(url: str, public_only: bool) -> None:
    if net_safety.url_error(url, resolve=False):
        raise CheckFailed("blocked_address")
    if await net_safety.validate_host_async(url):
        raise CheckFailed("blocked_address")
    if public_only and await net_safety.validate_host_async(url, public_only=True):
        raise CheckFailed("private_address")


def _is_tls_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


async def request_json(client: httpx.AsyncClient, method: str, url: str, public_only: bool, *,
                       data: dict | None = None, headers: dict | None = None,
                       error_json: bool = False) -> tuple[int, Any]:
    """``(status, parsed JSON)`` of one request through the guarded client.

    Every URL — the issuer's and every endpoint its discovery document names
    — gets the same address check before the request (and the transport's
    at connect time). Redirects are refused, the answer is read up to
    :data:`MAX_BYTES`. A status other than 200 raises ``http_status``, unless
    ``error_json``: then a 4xx answer with a JSON body is returned too (the
    token endpoint reports ``invalid_grant`` and the like that way).
    Callers bound the whole exchange with one deadline.
    """
    await _precheck(url, public_only)
    try:
        async with client.stream(method, url, data=data, headers=headers) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise CheckFailed("redirect")
            if status != 200 and not (error_json and 400 <= status < 500):
                raise CheckFailed("http_status")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise CheckFailed("too_large")
    except CheckFailed:
        raise
    except net_safety.UnsafeTargetError as exc:
        raise CheckFailed("private_address" if "public addresses" in str(exc)
                          else "blocked_address") from None
    except httpx.TimeoutException:
        raise CheckFailed("timeout") from None
    except httpx.HTTPError as exc:
        raise CheckFailed("tls" if _is_tls_error(exc) else "cannot_connect") from None
    except ssl.SSLError:
        raise CheckFailed("tls") from None
    try:
        return status, json.loads(bytes(body))
    except ValueError:
        if status != 200:
            raise CheckFailed("http_status") from None
        raise CheckFailed("not_json") from None


async def _get_json(client: httpx.AsyncClient, url: str, public_only: bool) -> Any:
    return (await request_json(client, "GET", url, public_only))[1]


def _https(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) <= 2048


def check_document(doc: Any, issuer: str) -> tuple[dict, list[str], list[str]]:
    """``(kept fields, accepted algorithms, notes)``. Raises :class:`CheckFailed`."""
    if not isinstance(doc, dict):
        raise CheckFailed("not_discovery")
    stated = doc.get("issuer")
    if not isinstance(stated, str):
        raise CheckFailed("not_discovery")
    if stated != issuer:
        # Not echoed: whatever the target put there is not for the caller.
        raise CheckFailed("issuer_mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if key not in doc:
            raise CheckFailed("not_discovery")
        if not _https(doc[key]):
            raise CheckFailed("insecure_endpoint", f"({key})")
    if "userinfo_endpoint" in doc and not _https(doc["userinfo_endpoint"]):
        raise CheckFailed("insecure_endpoint", "(userinfo_endpoint)")
    response_types = doc.get("response_types_supported")
    if isinstance(response_types, list) and "code" not in response_types:
        raise CheckFailed("no_code_flow")
    notes: list[str] = []
    pkce = doc.get("code_challenge_methods_supported")
    if isinstance(pkce, list):
        if "S256" not in pkce:
            raise CheckFailed("no_pkce")
    else:
        notes.append("The provider does not list its PKCE methods; nodeglow sends S256 anyway.")
    algs = doc.get("id_token_signing_alg_values_supported")
    if not isinstance(algs, list):
        raise CheckFailed("not_discovery")
    accepted = [a for a in ACCEPTED_ALGORITHMS if a in algs]
    if not accepted:
        raise CheckFailed("no_supported_algorithm")
    kept = {k: doc[k] for k in KEPT_FIELDS if k in doc}
    return kept, accepted, notes


def usable_keys(jwks: Any) -> int:
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise CheckFailed("jwks_invalid")
    count = 0
    for key in jwks["keys"]:
        if not isinstance(key, dict) or key.get("use") not in (None, "sig"):
            continue
        if key.get("kty") == "RSA" and key.get("n") and key.get("e"):
            count += 1
        elif key.get("kty") == "EC" and key.get("crv") == "P-256" and key.get("x") and key.get("y"):
            count += 1
    if not count:
        raise CheckFailed("jwks_invalid", "(no RSA or P-256 signing key)")
    return count


def claim_notes(doc: dict, *, scopes: str, groups_claim: str) -> list[str]:
    notes: list[str] = []
    claims = doc.get("claims_supported")
    if isinstance(claims, list):
        for claim in ("email", "email_verified"):
            if claim not in claims:
                notes.append(f"The provider does not list the claim '{claim}'.")
        if groups_claim not in claims:
            notes.append(f"The provider does not list the claim '{groups_claim}': role rules "
                         "and required groups depend on it.")
    supported = doc.get("scopes_supported")
    if isinstance(supported, list):
        missing = [s for s in scopes.split() if s not in supported]
        if missing:
            notes.append(f"The provider does not list the scope(s) {', '.join(missing)}.")
    return notes


async def check(issuer: str, *, scopes: str, groups_claim: str, ca_pem: str | None,
                public_only: bool) -> CheckResult:
    """Fetch and check the discovery document and the signing keys, within
    :data:`DEADLINE` for everything."""
    url = issuer.rstrip("/") + DISCOVERY_PATH
    try:
        client = client_factory(public_only=public_only, ca_pem=ca_pem)
    except ssl.SSLError:
        raise CheckFailed("tls", "(the CA certificate cannot be used)") from None
    try:
        async with asyncio.timeout(DEADLINE):
            async with client:
                doc = await _get_json(client, url, public_only)
                kept, algorithms, notes = check_document(doc, issuer)
                jwks = await _get_json(client, kept["jwks_uri"], public_only)
                keys = usable_keys(jwks)
    except TimeoutError:
        raise CheckFailed("timeout") from None
    notes += claim_notes(kept, scopes=scopes, groups_claim=groups_claim)
    return CheckResult(discovery=kept, algorithms=algorithms, signing_keys=keys, notes=notes)
