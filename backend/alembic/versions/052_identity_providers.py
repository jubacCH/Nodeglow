"""Single sign-on per tenant (ee): identity providers, identities, flows.

See ``docs/specs/2026-11-14-sso-per-tenant-design.md``. Schema only; the
enterprise package reads and writes it, and nothing signs anybody in yet.

* ``identity_providers``, ``user_identities``, ``sso_flows`` (tenant-owned):
  the ``tenant_isolation`` policy and RLS like every tenant table.
  ``identity_providers.login_key`` and ``sso_flows.state_hash`` are unique
  across the installation (looked up before the tenant is known).
* ``sessions.identity_provider_id``: the provider a session was opened
  through; deleting the provider ends its sessions.
* Usernames grow to 255 characters wherever one is stored (a single sign-on
  user is ``<mail address>#<provider id>``): ``users.username``,
  ``audit_logs.username``, ``api_keys.created_by``,
  ``agent_install_tokens.created_by``, ``support_grants.granted_by_name`` /
  ``revoked_by_name``, ``incidents.acknowledged_by`` / ``feedback_by``.
  Widening a varchar is a catalogue change in PostgreSQL, no table rewrite.

Downgrade: drops the three tables and the session column; shrinks the
columns back, cutting stored names to the old width. It refuses while a
username longer than 64 characters exists, because cutting those could make
two users the same.

Revision ID: 052
Revises: 051
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None

TABLES = ("identity_providers", "user_identities", "sso_flows")

# (table, column, old width); the new width is 255 everywhere.
WIDENED = (
    ("users", "username", 64),
    ("audit_logs", "username", 64),
    ("api_keys", "created_by", 64),
    ("agent_install_tokens", "created_by", 64),
    ("support_grants", "granted_by_name", 64),
    ("support_grants", "revoked_by_name", 64),
    ("incidents", "acknowledged_by", 128),
    ("incidents", "feedback_by", 128),
)
NEW_WIDTH = 255


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in _inspector().get_columns(table))


def _create_tables() -> None:
    if not _has_table("identity_providers"):
        op.create_table(
            "identity_providers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("type", sa.String(16), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("login_key", sa.String(64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("issuer", sa.String(512), nullable=False),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("client_secret", sa.Text(), nullable=True),
            sa.Column("scopes", sa.String(255), nullable=False),
            sa.Column("groups_claim", sa.String(64), nullable=False),
            sa.Column("ca_pem", sa.Text(), nullable=True),
            sa.Column("discovery_json", sa.Text(), nullable=True),
            sa.Column("discovery_at", sa.DateTime(), nullable=True),
            sa.Column("jit_enabled", sa.Boolean(), nullable=False),
            sa.Column("allowed_domains", sa.Text(), nullable=False),
            sa.Column("required_groups", sa.Text(), nullable=False),
            sa.Column("role_rules", sa.Text(), nullable=False),
            sa.Column("default_role", sa.String(16), nullable=False),
            sa.Column("issuer_is_own_organisation", sa.Boolean(), nullable=False),
            sa.Column("session_hours", sa.Integer(), nullable=False),
            sa.Column("sso_required", sa.Boolean(), nullable=False),
            sa.Column("config_hash", sa.String(64), nullable=True),
            sa.Column("tested_hash", sa.String(64), nullable=True),
            sa.Column("tested_at", sa.DateTime(), nullable=True),
            sa.Column("tested_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                    name="fk_identity_providers_tenant"),
            sa.UniqueConstraint("login_key", name="identity_providers_login_key_key"),
            sa.UniqueConstraint("tenant_id", "id", name="uq_identity_providers_tenant_id_id"),
        )
    if not _has_table("user_identities"):
        op.create_table(
            "user_identities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("identity_provider_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("external_subject", sa.String(255), nullable=True),
            sa.Column("email", sa.String(320), nullable=True),
            sa.Column("display_name", sa.String(128), nullable=True),
            sa.Column("created_via", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                    name="fk_user_identities_tenant"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_identities_user",
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id", "identity_provider_id"],
                                    ["identity_providers.tenant_id", "identity_providers.id"],
                                    name="fk_user_identities_provider", ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "identity_provider_id", "external_subject",
                                name="uq_user_identities_subject"),
            sa.UniqueConstraint("tenant_id", "identity_provider_id", "user_id",
                                name="uq_user_identities_user"),
        )
        op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    if not _has_table("sso_flows"):
        op.create_table(
            "sso_flows",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("state_hash", sa.String(64), nullable=False),
            sa.Column("identity_provider_id", sa.Integer(), nullable=False),
            sa.Column("purpose", sa.String(8), nullable=False),
            sa.Column("browser_hash", sa.String(64), nullable=False),
            sa.Column("nonce", sa.String(64), nullable=False),
            sa.Column("code_verifier", sa.String(128), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("next_path", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_sso_flows_tenant"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sso_flows_user",
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id", "identity_provider_id"],
                                    ["identity_providers.tenant_id", "identity_providers.id"],
                                    name="fk_sso_flows_provider", ondelete="CASCADE"),
            sa.UniqueConstraint("state_hash", name="sso_flows_state_hash_key"),
        )
        op.create_index("ix_sso_flows_tenant_id", "sso_flows", ["tenant_id"])
        op.create_index("ix_sso_flows_created_at", "sso_flows", ["created_at"])


def _add_session_column() -> None:
    if _has_column("sessions", "identity_provider_id"):
        return
    op.add_column("sessions", sa.Column("identity_provider_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_sessions_identity_provider", "sessions", "identity_providers",
                          ["identity_provider_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_sessions_identity_provider_id", "sessions", ["identity_provider_id"])


def _resize(width_of) -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, column, old in WIDENED:
        if not _has_table(table) or not _has_column(table, column):
            continue
        width = width_of(old)
        extra = {}
        if postgres and width < NEW_WIDTH:
            # Shrinking: cut longer values instead of failing on them.
            extra["postgresql_using"] = f"left({column}, {width})"
        op.alter_column(table, column, type_=sa.String(width),
                        existing_nullable=(table, column) != ("users", "username"), **extra)


def _apply_rls() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    import config
    from services import pg_rls

    pg_rls.apply(bind, TABLES, enabled=config.RLS_ENABLED, login=pg_rls.app_login())


def upgrade() -> None:
    _create_tables()
    _add_session_column()
    _resize(lambda _old: NEW_WIDTH)
    _apply_rls()


def downgrade() -> None:
    bind = op.get_bind()
    too_long = bind.execute(sa.text(
        "SELECT count(*) FROM users WHERE length(username) > 64"
    )).scalar() or 0
    if too_long:
        raise RuntimeError(
            f"052 downgrade refused: {too_long} username(s) are longer than 64 characters "
            "(single sign-on users). Rename or delete them first."
        )
    if _has_column("sessions", "identity_provider_id"):
        op.drop_index("ix_sessions_identity_provider_id", table_name="sessions")
        op.drop_constraint("fk_sessions_identity_provider", "sessions", type_="foreignkey")
        op.drop_column("sessions", "identity_provider_id")
    if bind.dialect.name == "postgresql":
        from services import pg_rls

        pg_rls.remove(bind, [t for t in TABLES if _has_table(t)])
    for table in reversed(TABLES):
        if _has_table(table):
            op.drop_table(table)
    _resize(lambda old: old)
