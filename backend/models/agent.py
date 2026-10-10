"""Agent model — registered agent host configuration only.

Time-series snapshots live in ClickHouse `agent_metrics` table; query them
via `services.clickhouse_client` helpers.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, func

from models.base import Base
from models.tenant import TenantScoped, tenant_fk, tenant_parent_unique


class Agent(TenantScoped, Base):
    """A registered agent host that reports metrics."""
    __tablename__ = "agents"
    __tenant_index__ = False  # covered by uq_agents_tenant_id_id
    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(128), nullable=False)
    hostname   = Column(String(256), nullable=True)
    token      = Column(String(64), nullable=False, unique=True, index=True)
    platform   = Column(String(32), nullable=True)
    arch       = Column(String(32), nullable=True)
    agent_version = Column(String(16), nullable=True)
    enabled    = Column(Boolean, default=True)
    log_levels = Column(String(32), nullable=True, default="1,2,3")
    log_channels = Column(Text, nullable=True, default="System,Application")
    log_file_paths = Column(Text, nullable=True)
    agent_log_level = Column(String(16), nullable=True, default="errors")
    pending_command = Column(String(32), nullable=True)
    # A probe is an agent that also runs checks for hosts in its network,
    # rather than only reporting on the machine it sits on.
    is_probe   = Column(Boolean, default=False, nullable=False)
    probe_interval_seconds = Column(Integer, nullable=True)
    watched_services = Column(Text, nullable=True)
    # Latest reported state of the watched services plus the failure streaks,
    # JSON (see services.agent_services). NULL until the agent first reports
    # service states — always the case for agents that predate the feature.
    service_states = Column(Text, nullable=True)
    last_seen  = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    site_id    = Column(Integer, nullable=True)

    __table_args__ = (
        # Hostnames are unique per tenant: two customers may both run "srv01".
        Index("ix_agents_hostname_lower", "tenant_id", func.lower(hostname), unique=True),
        tenant_parent_unique("agents"),
        tenant_fk("site_id", "sites", name="fk_agents_site"),
    )
