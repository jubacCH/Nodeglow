"""Every settings key, declared once (multi-tenancy phase 3, "Settings split").

``settings`` holds **installation** keys: one value for the whole deployment
(listener ports, the URL agents call, LDAP in core, GeoIP, the AI provider
and its key, markers of one-time upgrades). ``tenant_settings`` holds
**tenant** keys: what a customer configures for itself (name, time zone,
thresholds, retention, notification channels, digest, AI opt-in, phpIPAM).

``models.settings.get_setting`` / ``set_setting`` route every key through
:func:`lookup`: a tenant key needs a tenant (the ambient tenant context, the
session's binding, or an explicit ``tenant_id``) and raises
``TenantContextError`` without one — the same fail-closed rule as the
session filter. A key that is not declared here raises
:class:`UnknownSettingError`; ``tests/test_settings_registry.py`` checks that
every key used in the code base is declared.

Per-user preferences (the dashboard layout) are not settings: they live in
``user_preferences``, one row per (user, tenant).

Adding a key: declare it below. A tenant key that already has rows in
``settings`` on existing installs also needs a migration moving them (see
revision 042, which froze the list of keys it moved).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

INSTALLATION = "installation"
TENANT = "tenant"

Scope = Literal["installation", "tenant"]
ValueType = Literal["str", "int", "float", "bool", "json"]


class UnknownSettingError(KeyError):
    """A settings key that is not declared in the registry."""


@dataclass(frozen=True, slots=True)
class SettingDef:
    key: str
    scope: Scope
    type: ValueType = "str"
    # Used when the caller of get_setting() passes no default of its own.
    default: str | None = None
    # The value is a secret, stored encrypted (encrypt_value) and never sent
    # back to the UI. Informational for the storage layer: callers encrypt.
    secret: bool = False
    # Tenant key that falls back to the installation row when the tenant has
    # none (an installation-wide relay that a tenant may override).
    fallback: bool = False
    # ``key`` is a prefix: the setting is ``<key><suffix>`` (e.g. per username).
    prefix: bool = False
    note: str = ""

    @property
    def is_tenant(self) -> bool:
        return self.scope == TENANT


def _i(key: str, type: ValueType = "str", default: str | None = None, **kw) -> SettingDef:
    return SettingDef(key, INSTALLATION, type, default, **kw)


def _t(key: str, type: ValueType = "str", default: str | None = None, **kw) -> SettingDef:
    return SettingDef(key, TENANT, type, default, **kw)


_DEFS: tuple[SettingDef, ...] = (
    # ── Installation ─────────────────────────────────────────────────────────
    _i("setup_complete", "bool", note="the setup wizard has run"),
    _i("syslog_port", "int", "1514", note="one listener per installation"),
    _i("agent_server_url", note="what agents are told to call"),
    _i("nodeglow_ip", note="Proxmox helper; revisit with probe-side integrations"),
    _i("public_url", note="external URL of this installation, used for links"),
    _i("geoip_enabled", "bool", "0"),
    _i("geoip_license_key", secret=True),
    _i("geoip_last_updated"),
    _i("disk_usage_history", "json", "[]", note="self-monitoring of the data volume"),
    _i("_lockout:", "json", "[]", prefix=True,
       note="failed logins per username; users are global, so is the lock"),
    _i("agent_enrollment_key", secret=True,
       note="legacy shared enrolment key; cannot carry a tenant"),
    # LDAP: installation-level in core (maps to the default tenant); per
    # tenant/provider identity providers are ee.
    _i("ldap_enabled", "bool", "0"),
    _i("ldap_server"),
    _i("ldap_bind_dn"),
    _i("ldap_bind_password", secret=True),
    _i("ldap_base_dn"),
    _i("ldap_user_filter", default="(&(objectClass=person)(sAMAccountName={username}))"),
    _i("ldap_display_attr", default="displayName"),
    _i("ldap_group_attr", default="memberOf"),
    _i("ldap_admin_group"),
    _i("ldap_editor_group"),
    _i("ldap_use_ssl", "bool", "0"),
    _i("ldap_start_tls", "bool", "0"),
    _i("ldap_tls_verify", "bool", "1"),
    # AI provider: the installation's (SaaS: the operator's) key and endpoint.
    # A per-tenant key override is ee. The opt-in itself is a tenant key.
    _i("claude_api_key", secret=True),
    _i("ai_provider", default="anthropic"),
    _i("ai_anthropic_model"),
    _i("ai_openai_base_url"),
    _i("ai_openai_api_key", secret=True),
    _i("ai_openai_model"),
    _i("ai_openai_api_version"),
    # One-time upgrade markers and ee licence state.
    _i("ai_optin_migrated", note="marker: AI opt-in upgrade ran"),
    _i("channel_secrets_encrypted_at", note="marker: legacy channel secrets encrypted"),
    _i("ee_license", note="ee licence file"),
    _i("ee_install_id", note="installation id the ee licence is bound to"),

    # ── Tenant ───────────────────────────────────────────────────────────────
    _t("site_name", default="NODEGLOW", note="the tenant's display name"),
    _t("timezone", default="UTC"),
    _t("ping_interval", "int", "60", note="global job cadence = fastest tenant (phase 5)"),
    _t("proxmox_interval", "int", "60", note="global job cadence = fastest tenant (phase 5)"),
    _t("latency_threshold_ms", "int", ""),
    _t("anomaly_threshold", "float", "2.0"),
    _t("proxmox_cpu_threshold", "int", "85"),
    _t("proxmox_ram_threshold", "int", "85"),
    _t("proxmox_disk_threshold", "int", "90"),
    _t("availability_target", "float"),
    _t("ping_retention_days", "int", "30"),
    _t("proxmox_retention_days", "int", "7"),
    _t("integration_retention_days", "int", "7"),
    _t("incident_event_retention_days", "int", "30"),
    _t("template_retention_days", "int", "90"),
    _t("template_singleton_retention_days", "int", "7"),
    _t("correlation_min_failures", "int", "3"),
    _t("correlation_min_cycles", "int", "2"),
    _t("predictor_min_confidence", "float"),
    _t("predictor_min_occurrences", "int"),
    _t("predictor_template_blacklist", "json"),
    _t("syslog_allowlist_only", "bool", "0"),
    _t("log_baseline_done_through", note="state: last hour folded into the volume baselines"),
    _t("agent_service_fail_reports", "int", "3"),
    # Notifications
    _t("notify_enabled", "bool", "0"),
    _t("notify_grace_minutes", "int", "5"),
    _t("notify_telegram_min_severity", default="all"),
    _t("notify_discord_min_severity", default="all"),
    _t("notify_webhook_min_severity", default="all"),
    _t("notify_email_min_severity", default="all"),
    _t("notify_teams_min_severity", default="all"),
    _t("notify_slack_min_severity", default="all"),
    _t("notify_ntfy_min_severity", default="all"),
    _t("telegram_bot_token", secret=True),
    _t("telegram_chat_id"),
    _t("discord_webhook_url", secret=True),
    _t("webhook_url", secret=True),
    _t("webhook_secret", secret=True),
    _t("teams_enabled", "bool", "0"),
    _t("teams_webhook_url", secret=True),
    _t("slack_enabled", "bool", "0"),
    _t("slack_webhook_url", secret=True),
    _t("ntfy_enabled", "bool", "0"),
    _t("ntfy_server_url", default="https://ntfy.sh"),
    _t("ntfy_topic"),
    _t("ntfy_token", secret=True),
    # SMTP: the tenant's own relay; falls back to an installation relay (SaaS)
    # when the tenant has none. Revision 042 moved existing values to tenant 1.
    _t("smtp_host", fallback=True),
    _t("smtp_port", "int", "587", fallback=True),
    _t("smtp_user", fallback=True),
    _t("smtp_password", secret=True, fallback=True),
    _t("smtp_from", fallback=True),
    _t("smtp_to"),
    # Digest and the (ee) daily AI summary
    _t("digest_enabled", "bool", "0"),
    _t("digest_day", "int", "0", note="global cron = first tenant's (phase 5)"),
    _t("digest_hour", "int", "9", note="global cron = first tenant's (phase 5)"),
    _t("daily_ai_summary_enabled", "bool", "0"),
    _t("daily_ai_summary_hour", "int", "8"),
    _t("daily_ai_summary_channels"),
    _t("daily_ai_summary_last_sent", note="state"),
    # AI opt-in (consent is per tenant) and what may leave the tenant
    _t("ai_enabled", "bool", "0"),
    _t("ai_enabled_by"),
    _t("ai_enabled_at"),
    _t("ai_redact_enabled", "bool", "1"),
    _t("ai_redact_hostnames", "bool", "0"),
    _t("ai_restore_placeholders", "bool", "1"),
    # phpIPAM import (really integration config; candidate for integration_configs)
    _t("phpipam_url"),
    _t("phpipam_app_id"),
    _t("phpipam_username"),
    _t("phpipam_password", secret=True),
    _t("phpipam_verify_ssl", "bool", "0"),
    _t("phpipam_sync_hours", "int", "0"),
)

REGISTRY: dict[str, SettingDef] = {d.key: d for d in _DEFS if not d.prefix}
PREFIXES: tuple[SettingDef, ...] = tuple(d for d in _DEFS if d.prefix)

if len(REGISTRY) + len(PREFIXES) != len(_DEFS):  # pragma: no cover — import-time guard
    raise RuntimeError("duplicate key in the settings registry")


def lookup(key: str) -> SettingDef:
    """The declaration of ``key``. Raises :class:`UnknownSettingError`."""
    d = REGISTRY.get(key)
    if d is not None:
        return d
    for p in PREFIXES:
        if key.startswith(p.key) and len(key) > len(p.key):
            return p
    raise UnknownSettingError(
        f"settings key {key!r} is not declared in services/settings_registry.py"
    )


def is_tenant_key(key: str) -> bool:
    return lookup(key).is_tenant


def tenant_keys() -> frozenset[str]:
    """Every (non-prefix) tenant-scoped key."""
    return frozenset(k for k, d in REGISTRY.items() if d.is_tenant)


def installation_keys() -> frozenset[str]:
    return frozenset(k for k, d in REGISTRY.items() if not d.is_tenant)
