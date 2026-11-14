"""Single sign-on per tenant: identity providers, linked identities, flow state.

See ``docs/specs/2026-11-14-sso-per-tenant-design.md``. Schema only in the
core, like ``provider_memberships`` and ``support_grants``: the enterprise
package (``nodeglow_ee.sso``) reads and writes these tables. All three are
tenant-owned, so they get the tenant filter, the composite parent keys and
the row-level-security policy like every tenant table; in tenant A's
context B's providers cannot even be read.

* ``identity_providers`` — one OIDC provider of one tenant. The client
  secret is stored under the tenant's data key (``services.tenant_keys``,
  ``v2:<key_id>:…``) and never returned by an API.
* ``user_identities`` — a user linked to a provider by the provider's
  subject (``sub``). The mail address is kept for display and invitations;
  it is never what a sign-in is matched on.
* ``sso_flows`` — one row per started sign-in, valid for minutes and
  consumed once (the replay protection).

``login_key`` and ``state_hash`` are unique across the installation on
purpose: both are looked up before the tenant is known, and the row found
names it (``models.tenant.GLOBAL_UNIQUE_COLUMNS``).
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)

from models.base import Base
from models.tenant import TenantScoped, tenant_fk, tenant_parent_unique

PROVIDER_TYPE_OIDC = "oidc"
AUTH_SOURCE_OIDC = "oidc"

# Usernames of users created by a sign-in end in "#<provider id>" (for
# example ``jdoe@acme.ch#7``), so they can never collide with a local or
# directory username, nor with another provider's. Local usernames of that
# shape are refused (:func:`is_reserved_username`).
_SSO_USERNAME_SUFFIX = re.compile(r"#\d+$")


def is_reserved_username(name: str | None) -> bool:
    """Whether ``name`` has the shape reserved for single sign-on users."""
    return bool(_SSO_USERNAME_SUFFIX.search((name or "").strip()))


RESERVED_USERNAME_ERROR = (
    "Usernames ending in '#' and a number are reserved for single sign-on accounts."
)


class IdentityProvider(TenantScoped, Base):
    """An OIDC identity provider of one tenant."""

    __tablename__ = "identity_providers"
    __tenant_index__ = False  # covered by uq_identity_providers_tenant_id_id

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(16), nullable=False, default=PROVIDER_TYPE_OIDC)
    name = Column(String(128), nullable=False)
    # Part of the tenant's sign-in address (/login/sso/<login_key>).
    login_key = Column(String(64), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=False)
    issuer = Column(String(512), nullable=False)
    client_id = Column(String(255), nullable=False)
    client_secret = Column(Text, nullable=True)          # v2:<key_id>:… under the tenant's key
    scopes = Column(String(255), nullable=False, default="openid profile email")
    groups_claim = Column(String(64), nullable=False, default="groups")
    ca_pem = Column(Text, nullable=True)                  # private CA for the provider's TLS
    discovery_json = Column(Text, nullable=True)          # last document that passed the checks
    discovery_at = Column(DateTime, nullable=True)
    jit_enabled = Column(Boolean, nullable=False, default=True)
    allowed_domains = Column(Text, nullable=False, default="[]")    # JSON list, lower case
    required_groups = Column(Text, nullable=False, default="[]")    # JSON list
    role_rules = Column(Text, nullable=False, default="[]")         # JSON list of {group, role}
    default_role = Column(String(16), nullable=False, default="readonly")  # readonly | editor | none
    # The admin confirmed that this issuer only authenticates people of their
    # organisation (needed to sign people in without domain or group limits).
    issuer_is_own_organisation = Column(Boolean, nullable=False, default=False)
    session_hours = Column(Integer, nullable=False, default=12)
    sso_required = Column(Boolean, nullable=False, default=False)
    # Hash of everything a test sign-in depends on, and the hash of the
    # configuration the last successful test ran with.
    config_hash = Column(String(64), nullable=True)
    tested_hash = Column(String(64), nullable=True)
    tested_at = Column(DateTime, nullable=True)
    tested_by = Column(Integer, nullable=True)   # user id; no FK (users may be purged)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(Integer, nullable=True)  # user id; no FK
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (tenant_parent_unique("identity_providers"),)


class UserIdentity(TenantScoped, Base):
    """A user linked to one identity provider by the provider's subject."""

    __tablename__ = "user_identities"
    __tenant_index__ = False  # covered by the two unique constraints

    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_provider_id = Column(Integer, nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="fk_user_identities_user"),
        nullable=False, index=True,
    )
    # The provider's ``sub``; NULL while an invitation waits for its first sign-in.
    external_subject = Column(String(255), nullable=True)
    email = Column(String(320), nullable=True)
    display_name = Column(String(128), nullable=True)
    created_via = Column(String(16), nullable=False)   # jit | invite | link
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    __table_args__ = (
        tenant_fk("identity_provider_id", "identity_providers",
                  name="fk_user_identities_provider", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "identity_provider_id", "external_subject",
                         name="uq_user_identities_subject"),
        UniqueConstraint("tenant_id", "identity_provider_id", "user_id",
                         name="uq_user_identities_user"),
    )


class SsoFlow(TenantScoped, Base):
    """A started sign-in: the state the callback must present, bound to one
    tenant, one provider and the browser that started it."""

    __tablename__ = "sso_flows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_hash = Column(String(64), nullable=False, unique=True)
    identity_provider_id = Column(Integer, nullable=False)
    purpose = Column(String(8), nullable=False)        # login | test | link
    browser_hash = Column(String(64), nullable=False)
    nonce = Column(String(64), nullable=False)
    code_verifier = Column(String(128), nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="fk_sso_flows_user"),
        nullable=True,
    )
    next_path = Column(String(512), nullable=True)
    # The redirect URI sent with the authorization request: the token request
    # must repeat it exactly, even if the public URL changes meanwhile (053).
    redirect_uri = Column(String(600), nullable=True)
    # The provider's config_hash when the flow started: a test counts only
    # for the settings it ran with (053).
    config_hash = Column(String(64), nullable=True)
    # Outcome of a test sign-in, shown to the admin who ran it: claims and
    # the decision preview, never tokens (053). Deleted with the flow.
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    consumed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        tenant_fk("identity_provider_id", "identity_providers",
                  name="fk_sso_flows_provider", ondelete="CASCADE"),
        Index("ix_sso_flows_created_at", "created_at"),
    )
