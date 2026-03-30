"""LDAP authentication service for Nodeglow."""
import asyncio
import logging
from dataclasses import dataclass

from ldap3 import (
    Connection, Server, ALL, SUBTREE,
    SIMPLE as AUTH_SIMPLE,
)
from ldap3.core.exceptions import (
    LDAPBindError, LDAPSocketOpenError, LDAPException,
)
from ldap3.utils.conv import escape_filter_chars

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
    admin_group: str     # CN of admin group
    editor_group: str    # CN of editor group
    use_ssl: bool = False
    start_tls: bool = False


@dataclass
class LdapUser:
    username: str
    display_name: str
    role: str  # admin | editor | readonly


def _resolve_role(entry, config: LdapConfig) -> str:
    """Determine Nodeglow role from LDAP group membership."""
    groups_raw = entry.entry_attributes_as_dict.get(config.group_attr, [])
    groups = [str(g).lower() for g in groups_raw]

    if config.admin_group:
        admin_cn = config.admin_group.lower()
        if any(admin_cn in g for g in groups):
            return "admin"

    if config.editor_group:
        editor_cn = config.editor_group.lower()
        if any(editor_cn in g for g in groups):
            return "editor"

    return "readonly"


def _ldap_authenticate(config: LdapConfig, username: str, password: str) -> LdapUser | None:
    """Synchronous LDAP bind + search. Runs in thread pool."""
    use_ssl = config.use_ssl or config.server.startswith("ldaps://")
    server = Server(config.server, use_ssl=use_ssl, get_info=ALL, connect_timeout=10)

    # Step 1: Service account bind to search for user
    try:
        svc_conn = Connection(
            server, user=config.bind_dn, password=config.bind_password,
            authentication=AUTH_SIMPLE, auto_bind=True,
            read_only=True, receive_timeout=10,
        )
    except (LDAPBindError, LDAPSocketOpenError) as exc:
        logger.error("LDAP service bind failed: %s", exc)
        return None

    if config.start_tls and not use_ssl:
        try:
            svc_conn.start_tls()
        except LDAPException as exc:
            logger.error("LDAP StartTLS failed: %s", exc)
            svc_conn.unbind()
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

    entry = svc_conn.entries[0]
    user_dn = str(entry.entry_dn)
    display_name = str(entry[config.display_attr]) if config.display_attr in entry.entry_attributes else username
    svc_conn.unbind()

    # Step 3: Verify user password via bind
    try:
        user_conn = Connection(
            server, user=user_dn, password=password,
            authentication=AUTH_SIMPLE, auto_bind=True,
            read_only=True, receive_timeout=10,
        )
        user_conn.unbind()
    except (LDAPBindError, LDAPSocketOpenError):
        logger.info("LDAP bind failed for user: %s", username)
        return None

    # Step 4: Determine role from groups
    # Re-bind as service account to read group membership (user bind may not have rights)
    role = "readonly"
    if config.group_attr and (config.admin_group or config.editor_group):
        try:
            svc_conn2 = Connection(
                server, user=config.bind_dn, password=config.bind_password,
                authentication=AUTH_SIMPLE, auto_bind=True,
                read_only=True, receive_timeout=10,
            )
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
    use_ssl = config.use_ssl or config.server.startswith("ldaps://")
    server = Server(config.server, use_ssl=use_ssl, get_info=ALL, connect_timeout=10)

    try:
        conn = Connection(
            server, user=config.bind_dn, password=config.bind_password,
            authentication=AUTH_SIMPLE, auto_bind=True,
            read_only=True, receive_timeout=10,
        )
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
