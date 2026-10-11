"""
Models package – re-exports all models and helpers for easy importing.

Usage:
    from models import Base, IntegrationConfig, Snapshot, PingHost, ...
    from models import get_setting, set_setting, encrypt_value, decrypt_value
"""
from models.base import (
    Base,
    AsyncSessionLocal,
    engine,
    encrypt_value,
    decrypt_value,
    get_db,
)
from models.settings import (
    Setting,
    User,
    Session,
    get_current_user,
    get_setting,
    set_setting,
    is_setup_complete,
)
from models.ping import PingHost
from models.integration import IntegrationConfig, Snapshot
from models.syslog import SyslogView
from models.incident import Incident, IncidentEvent
from models.log_template import LogTemplate, HostBaseline, PrecursorPattern
from models.agent import Agent
from models.agent_install_token import AgentInstallToken
from models.scanner import SubnetScanSchedule, SubnetScanLog
from models.credential import Credential
from models.snmp import SnmpMib, SnmpOid, SnmpHostConfig, SnmpResult
from models.api_key import ApiKey
from models.notification import NotificationLog
from models.alert_rule import AlertRule
from models.discovered_port import DiscoveredPort
from models.audit import AuditLog
from models.ai_usage import AiUsageLog
from models.backup import BackupJob, BackupHistory
from models.maintenance import MaintenanceWindow
from models.user_preference import UserPreference
from models.tenant import (
    TenantScoped, Provider, Tenant, Site, TenantMembership, ProviderMembership,
    TenantSetting, DEFAULT_PROVIDER_ID, DEFAULT_TENANT_ID, DEFAULT_SITE_ID,
    ensure_default_tenant,
)

# The tenant filter hooks into every SQLAlchemy session; installing it here
# means nothing can open a session before it is active.
from services import tenancy as _tenancy  # noqa: E402

_tenancy.install()

# Layer 2 (PostgreSQL row-level security, NODEGLOW_RLS=on): the tenant
# settings are set per transaction on the app's engine.
from services import pg_rls as _pg_rls  # noqa: E402

_pg_rls.install_if_enabled(engine)


async def init_db(bind=None):
    """Create all tables and the default provider/tenant/site (fresh installs).

    ``bind``: the engine to use (migrate.py passes the owner engine);
    default the app's.
    """
    async with (bind or engine).begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Every tenant-owned row references tenant 1; on a database built by the
    # migration chain these rows come from revision 039 and this is a no-op.
    if bind is None:
        async with AsyncSessionLocal() as db:
            await ensure_default_tenant(db)
    else:
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(bind, expire_on_commit=False) as db:
            await ensure_default_tenant(db)

    # Syslog messages are stored in ClickHouse — no PostgreSQL trigger needed


__all__ = [
    "Base", "AsyncSessionLocal", "engine",
    "encrypt_value", "decrypt_value", "get_db",
    "Setting", "User", "Session",
    "get_current_user", "get_setting", "set_setting", "is_setup_complete",
    "PingHost",
    "IntegrationConfig", "Snapshot",
    "SyslogView",
    "Incident", "IncidentEvent",
    "LogTemplate", "HostBaseline", "PrecursorPattern",
    "Agent", "AgentInstallToken",
    "SubnetScanSchedule", "SubnetScanLog",
    "Credential",
    "SnmpMib", "SnmpOid", "SnmpHostConfig", "SnmpResult",
    "ApiKey",
    "NotificationLog",
    "AlertRule",
    "DiscoveredPort",
    "AuditLog",
    "AiUsageLog",
    "BackupJob", "BackupHistory",
    "MaintenanceWindow",
    "UserPreference",
    "TenantScoped", "Provider", "Tenant", "Site", "TenantMembership",
    "ProviderMembership", "TenantSetting",
    "DEFAULT_PROVIDER_ID", "DEFAULT_TENANT_ID", "DEFAULT_SITE_ID",
    "ensure_default_tenant",
    "init_db",
]
