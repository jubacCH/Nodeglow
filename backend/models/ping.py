"""PingHost model — host configuration only.

Time-series ping check results live in ClickHouse `ping_checks` table; query
them via `services.clickhouse_client` helpers.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from models.base import Base
from models.tenant import TenantScoped, tenant_fk, tenant_parent_unique


class PingHost(TenantScoped, Base):
    __tablename__ = "ping_hosts"
    __tenant_index__ = False  # covered by uq_ping_hosts_tenant_id_id
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    name                 = Column(String, nullable=False)
    hostname             = Column(String, nullable=False)
    enabled              = Column(Boolean, default=True)
    check_type           = Column(String, default="icmp")   # icmp | http | https | tcp
    port                 = Column(Integer, nullable=True)
    latency_threshold_ms = Column(Float, nullable=True)
    maintenance          = Column(Boolean, default=False)
    maintenance_until    = Column(DateTime, nullable=True)
    ssl_expiry_days      = Column(Integer, nullable=True)
    port_error           = Column(Boolean, default=False)
    check_detail         = Column(Text, nullable=True)
    # JSON {check label: reason} for the checks that failed in the last cycle,
    # e.g. {"https": "status 503"}. Kept apart from check_detail, whose
    # {label: bool} shape the correlation engine and the UI rely on.
    check_errors         = Column(Text, nullable=True)
    # JSON options for http/https checks (method, path, expected status,
    # keywords, timeout, redirects, TLS verification). NULL = the defaults,
    # which match how HTTP checks behaved before the options existed.
    http_options         = Column(Text, nullable=True)
    ip_address          = Column(String, nullable=True)
    source               = Column(String, default="manual", index=True)
    source_detail        = Column(String, nullable=True)
    mac_address          = Column(String, nullable=True)
    parent_id            = Column(Integer, nullable=True)
    # Which probe checks this host. NULL means the core does, which is every
    # existing row — the feature stays inert until a host is assigned.
    probe_id             = Column(Integer, nullable=True, index=True)
    site_id              = Column(Integer, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        tenant_parent_unique("ping_hosts"),
        # Parent, probe and site must belong to the host's own tenant.
        tenant_fk("parent_id", "ping_hosts", name="fk_ping_hosts_parent"),
        # SET NULL (probe_id) only: removing a probe must not delete the hosts
        # it watched — they fall back to the core (see migration 034).
        tenant_fk("probe_id", "agents", name="fk_ping_hosts_probe_id", ondelete="SET NULL"),
        tenant_fk("site_id", "sites", name="fk_ping_hosts_site"),
    )
