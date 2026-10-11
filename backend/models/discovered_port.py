"""Discovered ports and their SSL certificates for host auto-discovery."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime,
    Index, Integer, String,
)

from models.base import Base
from models.tenant import TenantScoped, tenant_fk


class DiscoveredPort(TenantScoped, Base):
    """A port found open on a monitored host."""
    __tablename__ = "discovered_ports"
    __tenant_index__ = False  # covered by ix_disc_port_host_port

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, nullable=False)
    port = Column(Integer, nullable=False)
    protocol = Column(String(8), default="tcp")          # tcp | udp
    service = Column(String(64), nullable=True)           # guessed service name
    status = Column(String(16), default="new")            # new | monitored | dismissed
    has_ssl = Column(Boolean, default=False)
    ssl_issuer = Column(String(256), nullable=True)
    ssl_subject = Column(String(256), nullable=True)
    ssl_expiry_days = Column(Integer, nullable=True)
    ssl_expiry_date = Column(String(32), nullable=True)
    ssl_status = Column(String(16), default="new")        # new | monitored | dismissed
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    last_open = Column(Boolean, default=True)             # was it open on last scan?

    __table_args__ = (
        Index("ix_disc_port_host", "host_id"),
        Index("ix_disc_port_host_port", "tenant_id", "host_id", "port", unique=True),
        tenant_fk("host_id", "ping_hosts", name="fk_discovered_ports_host", ondelete="CASCADE"),
    )
