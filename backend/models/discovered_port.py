"""Discovered ports and their SSL certificates for host auto-discovery."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Index, Integer, SmallInteger, String, Text,
)

from models.base import Base


class DiscoveredPort(Base):
    """A port found open on a monitored host."""
    __tablename__ = "discovered_ports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, ForeignKey("ping_hosts.id", ondelete="CASCADE"), nullable=False)
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
        Index("ix_disc_port_host_port", "host_id", "port", unique=True),
    )
