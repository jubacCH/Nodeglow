"""Multi-tenancy, settings split: tenant keys move to tenant_settings.

``settings`` becomes installation-only. Every key the registry
(``services/settings_registry.py``) declares as tenant-scoped is copied from
``settings`` to ``tenant_settings`` of tenant 1 — value and ``encrypted``
flag unchanged — and removed from ``settings``. The list is frozen here: the
registry may grow later, this revision must not change with it.

The dashboard layout was one setting for the whole installation; it becomes a
per-user preference (``user_preferences.dashboard_layout``, one row per user
and tenant). Every existing user gets a copy of the current layout, so nobody
sees a different dashboard after the upgrade.

Downgrade copies tenant 1's values back into ``settings`` (and the layout of
the first user who has one) and drops the column.

Revision ID: 042
Revises: 041
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None

TENANT_ID = 1

# Tenant-scoped keys as of this revision (registry, 2026-10-11).
TENANT_KEYS = (
    "agent_service_fail_reports", "ai_enabled", "ai_enabled_at", "ai_enabled_by",
    "ai_redact_enabled", "ai_redact_hostnames", "ai_restore_placeholders",
    "anomaly_threshold", "availability_target", "correlation_min_cycles",
    "correlation_min_failures", "daily_ai_summary_channels", "daily_ai_summary_enabled",
    "daily_ai_summary_hour", "daily_ai_summary_last_sent", "digest_day", "digest_enabled",
    "digest_hour", "discord_webhook_url", "incident_event_retention_days",
    "integration_retention_days", "latency_threshold_ms", "log_baseline_done_through",
    "notify_discord_min_severity", "notify_email_min_severity", "notify_enabled",
    "notify_grace_minutes", "notify_ntfy_min_severity", "notify_slack_min_severity",
    "notify_teams_min_severity", "notify_telegram_min_severity",
    "notify_webhook_min_severity", "ntfy_enabled", "ntfy_server_url", "ntfy_token",
    "ntfy_topic", "phpipam_app_id", "phpipam_password", "phpipam_sync_hours",
    "phpipam_url", "phpipam_username", "phpipam_verify_ssl", "ping_interval",
    "ping_retention_days", "predictor_min_confidence", "predictor_min_occurrences",
    "predictor_template_blacklist", "proxmox_cpu_threshold", "proxmox_disk_threshold",
    "proxmox_interval", "proxmox_ram_threshold", "proxmox_retention_days", "site_name",
    "slack_enabled", "slack_webhook_url", "smtp_from", "smtp_host", "smtp_password",
    "smtp_port", "smtp_to", "smtp_user", "syslog_allowlist_only", "teams_enabled",
    "teams_webhook_url", "telegram_bot_token", "telegram_chat_id",
    "template_retention_days", "template_singleton_retention_days", "timezone",
    "webhook_secret", "webhook_url",
)
LAYOUT_KEY = "dashboard_layout"


def _keys_param():
    return sa.bindparam("keys", expanding=True)


def _has_column(table: str, column: str) -> bool:
    cols = sa.inspect(op.get_bind()).get_columns(table)
    return any(c["name"] == column for c in cols)


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()

    bind.execute(
        sa.text(
            "INSERT INTO tenant_settings (tenant_id, key, value, encrypted) "
            "SELECT :tid, s.key, s.value, COALESCE(s.encrypted, false) FROM settings s "
            "WHERE s.key IN :keys AND NOT EXISTS ("
            "  SELECT 1 FROM tenant_settings t WHERE t.tenant_id = :tid AND t.key = s.key)"
        ).bindparams(_keys_param()),
        {"tid": TENANT_ID, "keys": list(TENANT_KEYS)},
    )
    bind.execute(
        sa.text("DELETE FROM settings WHERE key IN :keys").bindparams(_keys_param()),
        {"keys": list(TENANT_KEYS)},
    )

    if not _has_column("user_preferences", "dashboard_layout"):
        op.add_column("user_preferences", sa.Column("dashboard_layout", sa.Text(), nullable=True))
    layout = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = :k"), {"k": LAYOUT_KEY}
    ).scalar()
    if layout:
        bind.execute(
            sa.text("UPDATE user_preferences SET dashboard_layout = :v "
                    "WHERE tenant_id = :tid AND dashboard_layout IS NULL"),
            {"v": layout, "tid": TENANT_ID},
        )
        bind.execute(
            sa.text(
                "INSERT INTO user_preferences (user_id, tenant_id, dashboard_layout, updated_at) "
                "SELECT u.id, :tid, :v, :now FROM users u WHERE NOT EXISTS ("
                "  SELECT 1 FROM user_preferences p WHERE p.user_id = u.id AND p.tenant_id = :tid)"
            ),
            {"v": layout, "tid": TENANT_ID, "now": now},
        )
    bind.execute(sa.text("DELETE FROM settings WHERE key = :k"), {"k": LAYOUT_KEY})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO settings (key, value, encrypted) "
            "SELECT t.key, t.value, t.encrypted FROM tenant_settings t "
            "WHERE t.tenant_id = :tid AND t.key IN :keys AND NOT EXISTS ("
            "  SELECT 1 FROM settings s WHERE s.key = t.key)"
        ).bindparams(_keys_param()),
        {"tid": TENANT_ID, "keys": list(TENANT_KEYS)},
    )
    bind.execute(
        sa.text("DELETE FROM tenant_settings WHERE tenant_id = :tid AND key IN :keys")
        .bindparams(_keys_param()),
        {"tid": TENANT_ID, "keys": list(TENANT_KEYS)},
    )
    if _has_column("user_preferences", "dashboard_layout"):
        layout = bind.execute(
            sa.text("SELECT dashboard_layout FROM user_preferences "
                    "WHERE tenant_id = :tid AND dashboard_layout IS NOT NULL "
                    "ORDER BY user_id LIMIT 1"),
            {"tid": TENANT_ID},
        ).scalar()
        if layout and bind.execute(
            sa.text("SELECT 1 FROM settings WHERE key = :k"), {"k": LAYOUT_KEY}
        ).first() is None:
            bind.execute(
                sa.text("INSERT INTO settings (key, value, encrypted) VALUES (:k, :v, false)"),
                {"k": LAYOUT_KEY, "v": layout},
            )
        op.drop_column("user_preferences", "dashboard_layout")
        # Rows that only existed for the layout carry nothing else now (a
        # missing row means "nothing recorded", which is what they say).
        bind.execute(sa.text("DELETE FROM user_preferences WHERE dashboard_seen_at IS NULL"))
