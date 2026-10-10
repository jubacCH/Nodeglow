"""LDAP authentication service for Nodeglow."""
import asyncio
import logging
import os
import ssl
from dataclasses import dataclass

from ldap3 import (
    AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND,
    Connection, Server, Tls, ALL, SUBTREE,
    SIMPLE as AUTH_SIMPLE,
)
from ldap3.core.exceptions import (
    LDAPBindError, LDAPSocketOpenError, LDAPException,
)
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

logger = logging.getLogger(__name__)


@dataclass
class LdapConfig:
    server: str          # ldap://host or ldaps://host
    bind_dn: str         # e.g. cn=admin,dc=example,dc=com
    bind_password: str
    base_dn: str         # e.g. dc=example,dc=com
    user_filter: str     # e.g. (&(objectClass=person)(sAMAccountName={username}))
    display_attr: str    # attribute for display name, e.g. displayName
    group_attr: str      # attribute for groups, e.g. memberOf
    admin_group: str     # full DN or CN of the admin group
    editor_group: str    # full DN or CN of the editor group
    use_ssl: bool = False
    start_tls: bool = False
    # Verify the server certificate (LDAPS and StartTLS). Turning this off
    # lets anyone on the path read every bind password — opt-out only for a
    # lab directory with a self-signed cert; better point LDAP_CA_CERTS_FILE
    # at your CA instead.
    tls_verify: bool = True
    ca_certs_file: str | None = None


@dataclass
class LdapUser:
    username: str
    display_name: str
    role: str  # admin | editor | readonly


# ── Transport ────────────────────────────────────────────────────────────────


def _uses_ssl(config: LdapConfig) -> bool:
    return config.use_ssl or config.server.lower().startswith("ldaps://")


def _make_server(config: LdapConfig) -> Server:
    ca_file = config.ca_certs_file or os.environ.get("LDAP_CA_CERTS_FILE") or None
    tls = Tls(
        validate=ssl.CERT_REQUIRED if config.tls_verify else ssl.CERT_NONE,
        ca_certs_file=ca_file,
    )
    if not config.tls_verify:
        logger.warning("LDAP certificate verification is DISABLED (ldap_tls_verify=0)")
    return Server(config.server, use_ssl=_uses_ssl(config), tls=tls,
                  get_info=ALL, connect_timeout=10)


def _auto_bind_mode(config: LdapConfig):
    # StartTLS has to happen BEFORE the bind, or the bind DN and password
    # cross the wire in clear text and the upgrade protects nothing.
    if config.start_tls and not _uses_ssl(config):
        return AUTO_BIND_TLS_BEFORE_BIND
    return AUTO_BIND_NO_TLS


def _connect(server: Server, config: LdapConfig, user: str, password: str) -> Connection:
    return Connection(
        server, user=user, password=password,
        authentication=AUTH_SIMPLE, auto_bind=_auto_bind_mode(config),
        read_only=True, receive_timeout=10,
    )


# ── Group → role mapping ─────────────────────────────────────────────────────


def _norm_dn(dn: str) -> str | None:
    """Canonical lower-case DN, or None if it does not parse as a DN."""
    try:
        parts = parse_dn(dn.strip(), strip=True)
    except Exception:
        return None
    if not parts:
        return None
    return ",".join(f"{attr.strip().lower()}={value.strip().lower()}" for attr, value, _sep in parts)


def _cn_of(group: str) -> str:
    """The group's own name: the value of the first RDN, or the plain string."""
    try:
        parts = parse_dn(group.strip(), strip=True)
    except Exception:
        parts = []
    if parts and "=" in group:
        return parts[0][1].strip().lower()
    return group.strip().lower()


def _group_matches(configured: str, groups: list[str]) -> bool:
    """Exact match, never substring.

    ``configured`` may be a full DN (matched against the full DN, case- and
    whitespace-insensitively) or a bare CN (matched against each group's first
    RDN value). The old ``admin_cn in group_dn`` check made "Admins" match
    "CN=NotAdmins,…" and "CN=Users,OU=Admins,…".
    """
    configured = (configured or "").strip()
    if not configured:
        return False
    if "=" in configured:
        want = _norm_dn(configured)
        return want is not None and any(_norm_dn(g) == want for g in groups)
    want = configured.lower()
    return any(_cn_of(g) == want for g in groups)


def _resolve_role(entry, config: LdapConfig) -> str:
    """Determine Nodeglow role from LDAP group membership."""
    groups_raw = entry.entry_attributes_as_dict.get(config.group_attr, [])
    groups = [str(g) for g in groups_raw]

    if config.admin_group and _group_matches(config.admin_group, groups):
        return "admin"
    if config.editor_group and _group_matches(config.editor_group, groups):
        return "editor"
    return "readonly"


# ── Authentication ───────────────────────────────────────────────────────────


def _ldap_authenticate(config: LdapConfig, username: str, password: str) -> LdapUser | None:
    """Synchronous LDAP bind + search. Runs in thread pool."""
    # Refuse empty/whitespace-only passwords. LDAP simple bind treats an empty
    # password as an "unauthenticated bind" which succeeds against many
    # directories without verifying the user — an authentication bypass.
    if not password or not password.strip():
        logger.info("LDAP auth refused empty password for user: %s", username)
        return None

    server = _make_server(config)

    # Step 1: Service account bind to search for user
    try:
        svc_conn = _connect(server, config, config.bind_dn, config.bind_password)
    except (LDAPBindError, LDAPSocketOpenError) as exc:
        logger.error("LDAP service bind failed: %s", exc)
        return None
    except LDAPException as exc:  # incl. StartTLS / certificate failures
        logger.error("LDAP connection failed (TLS?): %s", exc)
        return None

    # Step 2: Search for the user
    search_filter = config.user_filter.replace("{username}", escape_filter_chars(username))
    attrs = ["dn", config.display_attr]
    if config.group_attr:
        attrs.append(config.group_attr)

    try:
        svc_conn.search(config.base_dn, search_filter, SUBTREE, attributes=attrs)
    except LDAPException as exc:
        logger.error("LDAP search failed: %s", exc)
        svc_conn.unbind()
        return None

    if not svc_conn.entries:
        logger.info("LDAP user not found: %s", username)
        svc_conn.unbind()
        return None
    if len(svc_conn.entries) > 1:
        # An ambiguous filter must not let the first hit decide who logs in.
        logger.warning("LDAP filter matched %d entries for %s — refusing", len(svc_conn.entries), username)
        svc_conn.unbind()
        return None

    entry = svc_conn.entries[0]
    user_dn = str(entry.entry_dn)
    display_name = str(entry[config.display_attr]) if config.display_attr in entry.entry_attributes else username
    svc_conn.unbind()

    # Step 3: Verify user password via bind (same transport security as the
    # service bind — StartTLS before the bind when configured).
    try:
        user_conn = _connect(server, config, user_dn, password)
        user_conn.unbind()
    except (LDAPBindError, LDAPSocketOpenError):
        logger.info("LDAP bind failed for user: %s", username)
        return None
    except LDAPException as exc:
        logger.error("LDAP user bind failed (TLS?) for %s: %s", username, exc)
        return None

    # Step 4: Determine role from groups
    # Re-bind as service account to read group membership (user bind may not have rights)
    role = "readonly"
    if config.group_attr and (config.admin_group or config.editor_group):
        try:
            svc_conn2 = _connect(server, config, config.bind_dn, config.bind_password)
            svc_conn2.search(config.base_dn, search_filter, SUBTREE,
                             attributes=[config.group_attr])
            if svc_conn2.entries:
                role = _resolve_role(svc_conn2.entries[0], config)
            svc_conn2.unbind()
        except LDAPException as exc:
            logger.warning("LDAP group lookup failed: %s", exc)

    return LdapUser(username=username, display_name=display_name, role=role)


async def authenticate_ldap(config: LdapConfig, username: str, password: str) -> LdapUser | None:
    """Async wrapper — runs LDAP I/O in a thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _ldap_authenticate, config, username, password)


def _test_connection(config: LdapConfig) -> dict:
    """Test LDAP connectivity and return diagnostic info."""
    server = _make_server(config)

    try:
        conn = _connect(server, config, config.bind_dn, config.bind_password)
    except LDAPSocketOpenError as exc:
        return {"ok": False, "error": f"Cannot reach LDAP server: {exc}"}
    except LDAPBindError as exc:
        return {"ok": False, "error": f"Bind DN/password rejected: {exc}"}
    except LDAPException as exc:
        return {"ok": False, "error": str(exc)}

    # Count users matching filter
    # Intentional wildcard — used to count all matching users during connection test
    test_filter = config.user_filter.replace("{username}", "*")
    try:
        conn.search(config.base_dn, test_filter, SUBTREE,
                     attributes=[config.display_attr], size_limit=100)
        user_count = len(conn.entries)
    except LDAPException:
        user_count = -1

    conn.unbind()
    return {"ok": True, "users_found": user_count}


async def test_ldap_connection(config: LdapConfig) -> dict:
    """Async wrapper for connection test."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _test_connection, config)
