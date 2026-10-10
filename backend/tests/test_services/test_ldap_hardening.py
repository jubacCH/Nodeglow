"""LDAP: verified TLS, StartTLS before every bind, exact group match, no
takeover of local accounts, fail-closed on an undecryptable bind password."""
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.ldap_auth as la
from services.ldap_auth import LdapConfig, LdapUser, _group_matches


def _cfg(**kw):
    base = dict(server="ldap://dc.lan", bind_dn="cn=svc,dc=lan", bind_password="pw",
                base_dn="dc=lan", user_filter="(uid={username})", display_attr="displayName",
                group_attr="memberOf", admin_group="", editor_group="")
    base.update(kw)
    return LdapConfig(**base)


# ── Group matching ──────────────────────────────────────────────────────────

GROUPS = ["CN=NotAdmins,OU=Groups,DC=lan", "CN=Users,OU=Admins,DC=lan", "CN=Editors,OU=Groups,DC=lan"]


@pytest.mark.parametrize("configured", ["Admins", "admin", "CN=Admins,OU=Groups,DC=lan", "dmins"])
def test_substrings_no_longer_match(configured):
    assert not _group_matches(configured, GROUPS)


@pytest.mark.parametrize("configured", [
    "Editors", "editors", "CN=Editors,OU=Groups,DC=lan", "cn=editors, ou=groups, dc=lan",
])
def test_exact_cn_or_dn_matches(configured):
    assert _group_matches(configured, GROUPS)


def test_resolve_role_uses_exact_match():
    entry = SimpleNamespace(entry_attributes_as_dict={"memberOf": GROUPS})
    assert la._resolve_role(entry, _cfg(admin_group="Admins", editor_group="Editors")) == "editor"
    assert la._resolve_role(entry, _cfg(admin_group="Admins")) == "readonly"


# ── Transport ───────────────────────────────────────────────────────────────


def test_server_verifies_certificates_by_default():
    server = la._make_server(_cfg(server="ldaps://dc.lan"))
    assert server.ssl is True
    assert server.tls.validate == ssl.CERT_REQUIRED


def test_tls_verify_opt_out():
    server = la._make_server(_cfg(server="ldaps://dc.lan", tls_verify=False))
    assert server.tls.validate == ssl.CERT_NONE


def test_starttls_happens_before_bind():
    assert la._auto_bind_mode(_cfg(start_tls=True)) == la.AUTO_BIND_TLS_BEFORE_BIND
    assert la._auto_bind_mode(_cfg(start_tls=True, server="ldaps://dc.lan")) == la.AUTO_BIND_NO_TLS
    assert la._auto_bind_mode(_cfg()) == la.AUTO_BIND_NO_TLS


def test_every_bind_uses_starttls_when_configured():
    """Service bind, user bind and group re-bind all upgrade before binding."""
    entry = MagicMock()
    entry.entry_dn = "uid=alice,dc=lan"
    entry.entry_attributes = []
    entry.entry_attributes_as_dict = {"memberOf": ["CN=Admins,DC=lan"]}
    conn = MagicMock()
    conn.entries = [entry]
    with patch.object(la, "Connection", return_value=conn) as C:
        user = la._ldap_authenticate(_cfg(start_tls=True, admin_group="Admins"), "alice", "secret")
    assert user is not None and user.role == "admin"
    binds = C.call_args_list
    assert len(binds) == 3
    assert [b.kwargs["auto_bind"] for b in binds] == [la.AUTO_BIND_TLS_BEFORE_BIND] * 3
    assert binds[1].kwargs["user"] == "uid=alice,dc=lan"
    conn.start_tls.assert_not_called()  # no after-the-fact upgrade


def test_ambiguous_user_filter_is_refused():
    conn = MagicMock()
    conn.entries = [MagicMock(), MagicMock()]
    with patch.object(la, "Connection", return_value=conn) as C:
        assert la._ldap_authenticate(_cfg(), "alice", "secret") is None
    assert len(C.call_args_list) == 1  # never tried the user bind


# ── routers.auth integration ────────────────────────────────────────────────


async def _enable_ldap(db, bind_password_enc):
    from models.settings import set_setting
    await set_setting(db, "ldap_enabled", "1")
    await set_setting(db, "ldap_server", "ldaps://dc.lan")
    await set_setting(db, "ldap_bind_password", bind_password_enc)


async def test_undecryptable_bind_password_fails_closed(db):
    from routers.auth import LdapConfigError, _get_ldap_config, _try_ldap_login
    await _enable_ldap(db, "not-a-fernet-token")
    with pytest.raises(LdapConfigError):
        await _get_ldap_config(db)
    with patch("services.ldap_auth.authenticate_ldap", new=AsyncMock()) as auth:
        assert await _try_ldap_login(db, "alice", "pw") == (None, False)
    auth.assert_not_awaited()  # the ciphertext is never sent anywhere


async def test_ldap_does_not_take_over_local_account(db):
    import bcrypt
    from database import User
    from models.base import encrypt_value
    from routers.auth import _try_ldap_login

    await _enable_ldap(db, encrypt_value("svcpw"))
    db.add(User(username="admin", password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(4)).decode(),
                role="admin", auth_source="local"))
    await db.commit()
    fake = AsyncMock(return_value=LdapUser(username="admin", display_name="Evil", role="readonly"))
    with patch("services.ldap_auth.authenticate_ldap", new=fake):
        user, created = await _try_ldap_login(db, "admin", "directory-pw")
    assert user is None and created is False
    row = (await db.execute(__import__("sqlalchemy").select(User).where(User.username == "admin"))).scalar_one()
    assert row.auth_source == "local" and row.role == "admin" and row.display_name is None


async def test_existing_ldap_user_is_updated(db):
    from database import User
    from models.base import encrypt_value
    from routers.auth import _try_ldap_login

    await _enable_ldap(db, encrypt_value("svcpw"))
    db.add(User(username="bob", password_hash="x", role="readonly", auth_source="ldap"))
    await db.commit()
    fake = AsyncMock(return_value=LdapUser(username="bob", display_name="Bob", role="editor"))
    with patch("services.ldap_auth.authenticate_ldap", new=fake):
        user, created = await _try_ldap_login(db, "bob", "pw")
    assert user is not None and user.role == "editor" and not created
    assert fake.await_args.args[0].tls_verify is True
