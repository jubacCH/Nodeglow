"""Per-tenant envelope encryption (owner decision 7).

* ``tenant_keys`` (tenant-owned; row-level-security policy like every
  tenant table): one row per data key — ``wrapped_dek`` is the tenant's
  random 32-byte data key encrypted by the installation's key-encryption key
  (``kek_id`` ``sk1``: HKDF over the stretched SECRET_KEY), see
  ``services/tenant_keys.py``.
* Tenant 1 gets its data key.
* **No secret is rewritten here.** The updater runs the migration with the
  new image while the old backend still serves (sidecar ``step_migrate``
  before ``step_restart``) — and a rollback by ``NODEGLOW_VERSION`` alone
  runs the old code on the migrated database. The old code reads every
  secret with the installation key, so the values stay exactly as they are
  until the new backend has started: its startup pass
  (``services.tenant_keys.upgrade_legacy_secrets``, idempotent) moves them
  to the tenant keys — ``integration_configs.config_json``,
  ``credentials.data_json`` and the secret ``tenant_settings`` — and keeps
  doing so on every start. ``python -m services.tenant_keys upgrade`` does
  the same by hand.
* The downgrade re-encrypts every ``v2`` value it can read back under the
  installation key and drops ``tenant_keys``. Values under a destroyed key
  stay unreadable — they were before the downgrade, too. Going back to an
  older version **after** the new backend ran needs this downgrade (``alembic
  downgrade 044`` with the new image) first: from then on the secrets are
  under the tenant keys, which the old code cannot read.

Check afterwards: ``python -m services.tenant_keys verify``.

Revision ID: 046
Revises: 045
"""
from __future__ import annotations

import logging
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

DEFAULT_TENANT_ID = 1

# Secret tenant settings at this revision (settings registry, secret=True);
# frozen here, the live list is used by the app.
SECRET_SETTING_KEYS = (
    "discord_webhook_url", "ntfy_token", "phpipam_password", "slack_webhook_url",
    "smtp_password", "teams_webhook_url", "telegram_bot_token", "webhook_secret",
    "webhook_url",
)


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _create_table(bind) -> None:
    if _has_table(bind, "tenant_keys"):
        return  # a fresh install (create_all) or an earlier, interrupted run
    op.create_table(
        "tenant_keys",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("kek_id", sa.String(length=16), nullable=False),
        sa.Column("wrapped_dek", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_keys_tenant",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "key_id"),
    )


def _row_sources():
    """``(label, rows select, its parameters, update, primary-key name)`` per
    secret column. Selects return ``(pk, tenant_id, value)``; updates are
    guarded by the old value, so a row changed meanwhile is not overwritten."""
    keys = sa.bindparam("keys", expanding=True)
    return [
        ("integration_configs.config_json",
         sa.text("SELECT id, tenant_id, config_json FROM integration_configs ORDER BY id"), {},
         sa.text("UPDATE integration_configs SET config_json = :new "
                 "WHERE id = :pk AND config_json = :old"),
         "id"),
        ("credentials.data_json",
         sa.text("SELECT id, tenant_id, data_json FROM credentials ORDER BY id"), {},
         sa.text("UPDATE credentials SET data_json = :new WHERE id = :pk AND data_json = :old"),
         "id"),
        ("tenant_settings.value",
         sa.text("SELECT key, tenant_id, value FROM tenant_settings "
                 "WHERE encrypted OR key IN :keys ORDER BY tenant_id, key").bindparams(keys),
         {"keys": list(SECRET_SETTING_KEYS)},
         sa.text("UPDATE tenant_settings SET value = :new "
                 "WHERE tenant_id = :tenant_id AND key = :pk AND value = :old"),
         "key"),
    ]


def _active_key(bind, tenant_id: int, *, create: bool):
    """``(key_id, Fernet)`` of the tenant's active key; created if missing."""
    from services import tenant_keys as tk

    row = bind.execute(sa.text(
        "SELECT key_id, kek_id, wrapped_dek FROM tenant_keys WHERE tenant_id = :t "
        "AND retired_at IS NULL AND destroyed_at IS NULL AND wrapped_dek IS NOT NULL "
        "ORDER BY created_at DESC, key_id DESC LIMIT 1"
    ), {"t": tenant_id}).first()
    if row is not None:
        return row[0], tk.dek_fernet(tk.unwrap_dek(row[2], tenant_id, row[0], row[1]))
    if not create:
        return None
    key_id, dek = tk.new_key_id(), tk.new_dek()
    bind.execute(sa.text(
        "INSERT INTO tenant_keys (tenant_id, key_id, kek_id, wrapped_dek, created_at) "
        "VALUES (:t, :k, :kek, :w, :now)"
    ), {"t": tenant_id, "k": key_id, "kek": tk.KEK_ID,
        "w": tk.wrap_dek(dek, tenant_id, key_id), "now": datetime.utcnow()})
    log.info("046: created data key %s for tenant %s", key_id, tenant_id)
    return key_id, tk.dek_fernet(dek)


def migrate_up(bind) -> str | None:
    """Tenant 1's data key (idempotent). Rewrites no secret — see the module
    docstring: the old backend may still be running on this database.
    Returns the key id, or None on a database without tenant 1."""
    if bind.execute(sa.text("SELECT 1 FROM tenants WHERE id = :t"),
                    {"t": DEFAULT_TENANT_ID}).first() is None:
        return None
    key_id, _fernet = _active_key(bind, DEFAULT_TENANT_ID, create=True)
    log.info("046: tenant secrets stay under the installation key until the new backend "
             "starts (it moves them to the tenant keys; python -m services.tenant_keys verify)")
    return key_id


def migrate_down(bind) -> dict:
    """Every readable ``v2`` value back under the installation key."""
    from models.base import encrypt_value
    from services import tenant_keys as tk

    ferns: dict[tuple[int, str], object] = {}
    for tenant_id, key_id, kek_id, wrapped in bind.execute(sa.text(
        "SELECT tenant_id, key_id, kek_id, wrapped_dek FROM tenant_keys"
    )).all():
        if wrapped:
            try:
                ferns[(tenant_id, key_id)] = tk.dek_fernet(
                    tk.unwrap_dek(wrapped, tenant_id, key_id, kek_id))
            except tk.TenantKeyError:
                pass
    report: dict[str, dict[str, int]] = {}
    for label, select, params, update, _pk in _row_sources():
        counts = report.setdefault(label, {"converted": 0, "skipped": 0, "failed": 0})
        for pk_value, tenant_id, value in bind.execute(select, params).all():
            if not value or not value.startswith(tk.FORMAT_PREFIX):
                counts["skipped"] += 1
                continue
            try:
                key_id, token = tk.parse_v2(value)
                plain = ferns[(tenant_id, key_id)].decrypt(token.encode()).decode()
            except Exception:
                counts["failed"] += 1
                continue
            bind.execute(update, {"new": encrypt_value(plain), "old": value,
                                  "tenant_id": tenant_id, "pk": pk_value})
            counts["converted"] += 1
    for label, counts in report.items():
        log.info("046 downgrade: %s: %d back under the installation key, %d unreadable",
                 label, counts["converted"], counts["failed"])
    return report


def _apply_rls(bind) -> None:
    """Policy for the new table; RLS on/off like the other tenant tables
    (migrate.py brings everything in line with NODEGLOW_RLS afterwards)."""
    if bind.dialect.name != "postgresql":
        return
    from services import pg_rls

    pg_rls.ensure_policies(bind, ["tenant_keys"])
    state = pg_rls.rls_state(bind, ["tenant_settings"]).get("tenant_settings", (False, False))
    pg_rls.set_rls(bind, ["tenant_keys"], state[0])


def upgrade() -> None:
    bind = op.get_bind()
    _create_table(bind)
    _apply_rls(bind)
    migrate_up(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "tenant_keys"):
        return
    migrate_down(bind)
    op.drop_table("tenant_keys")
