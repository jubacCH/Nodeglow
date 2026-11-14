"""A provider's settings: validation of what an admin sends, and the shape
the API returns.

The client secret is write-only. It is encrypted with the tenant's data key
by the caller (:mod:`.api`) and appears in responses only as
``client_secret_set``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from models.identity import IdentityProvider

from nodeglow_ee import sso

RULE_ROLES = ("admin", "editor", "readonly")
# Never admin: nobody becomes admin because a default was left in place.
DEFAULT_ROLES = ("readonly", "editor", "none")

MAX_NAME = 128
MAX_ISSUER = 512
MAX_CLIENT_ID = 255
MAX_CLIENT_SECRET = 2048
MAX_SCOPES = 255
MAX_CA_PEM = 64 * 1024
MAX_CA_CERTS = 10
MAX_LIST = 50
MAX_RULES = 100
MAX_GROUP = 255
MIN_SESSION_HOURS = 1
MAX_SESSION_HOURS = 168
DEFAULT_SCOPES = "openid profile email"

_LOGIN_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$")
_GROUPS_CLAIM = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
# RFC 6749 scope-token: printable ASCII except space, '"' and '\'.
_SCOPE = re.compile(r'^[\x21\x23-\x5B\x5D-\x7E]+$')
_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_DOMAIN = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")
_KEY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

FIELDS = frozenset({
    "name", "issuer", "client_id", "client_secret", "clear_client_secret", "scopes",
    "groups_claim", "ca_pem", "login_key", "jit_enabled", "allowed_domains",
    "required_groups", "role_rules", "default_role", "issuer_is_own_organisation",
    "session_hours", "enabled", "sso_required",
})
REQUIRED_ON_CREATE = ("name", "issuer", "client_id")
# What a test sign-in depends on; changing any of it makes a tested
# provider untested again (config_hash).
TESTED_FIELDS = ("issuer", "client_id", "scopes", "groups_claim", "ca_pem")

NOT_RELEASED = ("Sign-in through single sign-on is not available in this version yet. "
                "Providers can be prepared and checked; they cannot be enabled.")


class ConfigError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class Changes:
    """Validated column values, plus the client secret in plain text (to be
    encrypted by the caller), ``None`` = leave it, ``""`` = clear it."""

    values: dict[str, Any]
    client_secret: str | None = None


def _str(body: dict, key: str, limit: int, *, required: bool = False) -> str | None:
    if key not in body:
        if required:
            raise ConfigError(f"'{key}' is required.", key)
        return None
    value = body[key]
    if not isinstance(value, str):
        raise ConfigError(f"'{key}' must be text.", key)
    value = value.strip()
    if required and not value:
        raise ConfigError(f"'{key}' must not be empty.", key)
    if len(value) > limit:
        raise ConfigError(f"'{key}' is longer than {limit} characters.", key)
    return value


def _bool(body: dict, key: str) -> bool | None:
    if key not in body:
        return None
    if not isinstance(body[key], bool):
        raise ConfigError(f"'{key}' must be true or false.", key)
    return body[key]


def _list(body: dict, key: str) -> list | None:
    if key not in body:
        return None
    value = body[key]
    if not isinstance(value, list):
        raise ConfigError(f"'{key}' must be a list.", key)
    return value


def validate_issuer(value: str) -> str:
    """An https URL without query, fragment or credentials. Kept exactly as
    entered (apart from surrounding blanks): the discovery document must
    state the same string, character for character."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        parsed.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        raise ConfigError("The issuer is not a valid URL.", "issuer") from None
    if parsed.scheme != "https":
        raise ConfigError("The issuer must be an https:// URL.", "issuer")
    if not host:
        raise ConfigError("The issuer has no host name.", "issuer")
    if parsed.username or parsed.password:
        raise ConfigError("The issuer must not contain a user name or password.", "issuer")
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ConfigError("The issuer must not contain a query or fragment.", "issuer")
    if any(ch.isspace() for ch in value):
        raise ConfigError("The issuer must not contain blanks.", "issuer")
    return value


def _scopes(value: str) -> str:
    tokens: list[str] = []
    for token in value.split():
        if not _SCOPE.match(token):
            raise ConfigError(f"'{token}' is not a valid scope.", "scopes")
        if token not in tokens:
            tokens.append(token)
    if "openid" not in tokens:
        raise ConfigError("The scopes must include 'openid'.", "scopes")
    joined = " ".join(tokens)
    if len(joined) > MAX_SCOPES:
        raise ConfigError(f"The scopes are longer than {MAX_SCOPES} characters.", "scopes")
    return joined


def _ca_pem(value: str) -> str | None:
    if not value:
        return None
    if len(value) > MAX_CA_PEM:
        raise ConfigError("The CA certificate is too large.", "ca_pem")
    from cryptography import x509

    try:
        certs = x509.load_pem_x509_certificates(value.encode())
    except ValueError:
        raise ConfigError("The CA certificate is not a PEM certificate.", "ca_pem") from None
    if not certs or len(certs) > MAX_CA_CERTS:
        raise ConfigError(f"Give one to {MAX_CA_CERTS} PEM certificates.", "ca_pem")
    return value


def _domains(items: list) -> list[str]:
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ConfigError("Mail domains must be text.", "allowed_domains")
        domain = item.strip().lower().lstrip("@").rstrip(".")
        if not _DOMAIN.match(domain):
            raise ConfigError(f"'{item}' is not a mail domain.", "allowed_domains")
        if domain not in out:
            out.append(domain)
    if len(out) > MAX_LIST:
        raise ConfigError(f"At most {MAX_LIST} mail domains.", "allowed_domains")
    return out


def _groups(items: list, field: str) -> list[str]:
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("Groups must be non-empty text.", field)
        group = item.strip()
        if len(group) > MAX_GROUP:
            raise ConfigError(f"A group is longer than {MAX_GROUP} characters.", field)
        if group not in out:
            out.append(group)
    if len(out) > MAX_LIST:
        raise ConfigError(f"At most {MAX_LIST} groups.", field)
    return out


def _rules(items: list) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"group", "role"}:
            raise ConfigError("Each role rule needs exactly 'group' and 'role'.", "role_rules")
        group, role = item["group"], item["role"]
        if not isinstance(group, str) or not group.strip() or len(group.strip()) > MAX_GROUP:
            raise ConfigError("A role rule needs a group (up to 255 characters).", "role_rules")
        if role not in RULE_ROLES:
            raise ConfigError(f"The role of a rule must be one of {', '.join(RULE_ROLES)}.",
                              "role_rules")
        key = (group.strip(), role)
        if key not in seen:
            seen.add(key)
            out.append({"group": key[0], "role": role})
    if len(out) > MAX_RULES:
        raise ConfigError(f"At most {MAX_RULES} role rules.", "role_rules")
    return out


def validate(body: Any, *, creating: bool) -> Changes:
    """The column values ``body`` asks for. Raises :class:`ConfigError`."""
    if not isinstance(body, dict):
        raise ConfigError("Send a JSON object.")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise ConfigError(f"Unknown field '{unknown[0]}'.", unknown[0])
    values: dict[str, Any] = {}

    name = _str(body, "name", MAX_NAME, required=creating)
    if name is not None:
        if not name:
            raise ConfigError("'name' must not be empty.", "name")
        values["name"] = name
    issuer = _str(body, "issuer", MAX_ISSUER, required=creating)
    if issuer is not None:
        values["issuer"] = validate_issuer(issuer)
    client_id = _str(body, "client_id", MAX_CLIENT_ID, required=creating)
    if client_id is not None:
        if not client_id or any(ch.isspace() for ch in client_id):
            raise ConfigError("The client ID must not be empty or contain blanks.", "client_id")
        values["client_id"] = client_id

    secret: str | None = None
    if "client_secret" in body:
        raw = body["client_secret"]
        if not isinstance(raw, str):
            raise ConfigError("'client_secret' must be text.", "client_secret")
        if len(raw) > MAX_CLIENT_SECRET:
            raise ConfigError("The client secret is too long.", "client_secret")
        # Blank leaves the stored secret alone (the form never shows it).
        secret = raw.strip() or None
    if _bool(body, "clear_client_secret"):
        if secret:
            raise ConfigError("Either set a new client secret or clear it, not both.",
                              "client_secret")
        secret = ""

    scopes = _str(body, "scopes", 4 * MAX_SCOPES)
    if scopes is not None:
        values["scopes"] = _scopes(scopes)
    claim = _str(body, "groups_claim", 64)
    if claim is not None:
        if not _GROUPS_CLAIM.match(claim):
            raise ConfigError("The groups claim may contain letters, digits and _ . : / -.",
                              "groups_claim")
        values["groups_claim"] = claim
    if "ca_pem" in body:
        pem = body["ca_pem"]
        if pem is not None and not isinstance(pem, str):
            raise ConfigError("'ca_pem' must be text.", "ca_pem")
        values["ca_pem"] = _ca_pem((pem or "").strip())
    login_key = _str(body, "login_key", 64)
    if login_key is not None:
        login_key = login_key.lower()
        if not _LOGIN_KEY.match(login_key):
            raise ConfigError("The sign-in key may contain a-z, 0-9 and '-' (3 to 64 characters).",
                              "login_key")
        values["login_key"] = login_key

    for key in ("jit_enabled", "issuer_is_own_organisation"):
        flag = _bool(body, key)
        if flag is not None:
            values[key] = flag
    for key in ("enabled", "sso_required"):
        flag = _bool(body, key)
        if flag and not sso.SIGN_IN_RELEASED:
            raise ConfigError(NOT_RELEASED, key)
        if flag is not None:
            values[key] = flag

    domains = _list(body, "allowed_domains")
    if domains is not None:
        values["allowed_domains"] = json.dumps(_domains(domains))
    groups = _list(body, "required_groups")
    if groups is not None:
        values["required_groups"] = json.dumps(_groups(groups, "required_groups"))
    rules = _list(body, "role_rules")
    if rules is not None:
        values["role_rules"] = json.dumps(_rules(rules))
    if "default_role" in body:
        if body["default_role"] not in DEFAULT_ROLES:
            raise ConfigError(f"The default role must be one of {', '.join(DEFAULT_ROLES)}.",
                              "default_role")
        values["default_role"] = body["default_role"]
    if "session_hours" in body:
        hours = body["session_hours"]
        if isinstance(hours, bool) or not isinstance(hours, int) \
                or not MIN_SESSION_HOURS <= hours <= MAX_SESSION_HOURS:
            raise ConfigError(f"The session lifetime must be {MIN_SESSION_HOURS} to "
                              f"{MAX_SESSION_HOURS} hours.", "session_hours")
        values["session_hours"] = hours
    return Changes(values=values, client_secret=secret)


def new_login_key(tenant_slug: str | None) -> str:
    """``<tenant short name>-<six random characters>``: readable, and not
    guessable from the name alone (about 29 bits)."""
    base = re.sub(r"[^a-z0-9-]", "-", (tenant_slug or "sso").lower()).strip("-")[:40] or "sso"
    suffix = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(6))
    return f"{base}-{suffix}"


def config_hash(idp: IdentityProvider, secret_plain: str | None) -> str:
    """Hash of everything a test sign-in depends on. The secret enters as
    an HMAC under the installation key, never as itself."""
    from config import SECRET_KEY

    material = {k: getattr(idp, k) for k in TESTED_FIELDS}
    material["client_secret"] = hmac.new(
        SECRET_KEY.encode(), (secret_plain or "").encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def state_of(idp: IdentityProvider) -> str:
    # A stored "enabled" counts only once sign-in is released (a restore or a
    # hand-edited row cannot make a provider look live before that).
    if idp.enabled and sso.SIGN_IN_RELEASED:
        return "enabled"
    if idp.tested_hash and idp.tested_hash == idp.config_hash:
        return "tested"
    return "draft"


def open_to_everyone(idp: IdentityProvider) -> bool:
    """New accounts for whoever the issuer authenticates, unrestricted."""
    return bool(idp.jit_enabled and not _json_list(idp.allowed_domains)
                and not _json_list(idp.required_groups))


def blockers(idp: IdentityProvider, *, public_url: str) -> list[str]:
    """What stands between this provider and being enabled, in words."""
    out: list[str] = []
    if not sso.SIGN_IN_RELEASED:
        out.append(NOT_RELEASED)
    if not public_url:
        out.append("The installation's public address is not set (Settings → Notifications, "
                   "'Public URL'); the redirect address is built from it.")
    if not idp.client_secret:
        out.append("No client secret is stored.")
    if not idp.discovery_at:
        out.append("The connection has not been checked.")
    if open_to_everyone(idp) and not idp.issuer_is_own_organisation:
        out.append("New accounts are created for everyone the issuer signs in. Restrict them by "
                   "mail domain or group, or confirm that the issuer only signs in people of "
                   "your organisation.")
    if state_of(idp) == "draft":
        out.append("No test sign-in has passed with the current settings.")
    return out


def redirect_uri(public_url: str) -> str | None:
    return f"{public_url}/api/auth/sso/callback" if public_url else None


def sign_in_url(public_url: str, login_key: str) -> str | None:
    return f"{public_url}/login/sso/{login_key}" if public_url else None


def public(idp: IdentityProvider, *, public_url: str) -> dict:
    """What the API returns for a provider. Never the client secret."""
    try:
        discovery = json.loads(idp.discovery_json) if idp.discovery_json else None
    except ValueError:
        discovery = None
    return {
        "id": idp.id,
        "type": idp.type,
        "name": idp.name,
        "state": state_of(idp),
        "enabled": bool(idp.enabled) and sso.SIGN_IN_RELEASED,
        "login_key": idp.login_key,
        "sign_in_url": sign_in_url(public_url, idp.login_key),
        "issuer": idp.issuer,
        "client_id": idp.client_id,
        "client_secret_set": bool(idp.client_secret),
        "scopes": idp.scopes,
        "groups_claim": idp.groups_claim,
        "ca_pem": idp.ca_pem or "",
        "jit_enabled": bool(idp.jit_enabled),
        "allowed_domains": _json_list(idp.allowed_domains),
        "required_groups": _json_list(idp.required_groups),
        "role_rules": _json_list(idp.role_rules),
        "default_role": idp.default_role,
        "issuer_is_own_organisation": bool(idp.issuer_is_own_organisation),
        "session_hours": idp.session_hours,
        "sso_required": bool(idp.sso_required),
        "discovery": discovery,
        "discovery_at": _iso(idp.discovery_at),
        "tested_at": _iso(idp.tested_at),
        "created_at": _iso(idp.created_at),
        "updated_at": _iso(idp.updated_at),
        "blockers": blockers(idp, public_url=public_url),
    }
