"""Bandwidth sample model — stores per-interface traffic data from agents and integrations."""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String

from models.base import Base


class BandwidthSample(Base):
    """One bandwidth measurement for a specific network interface."""
    __tablename__ = "bandwidth_samples"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    timestamp      = Column(DateTime, nullable=False, index=True, default=datetime.utcnow)
    source_type    = Column(String(32), nullable=False)   # "agent", "proxmox", "unifi"
    source_id      = Column(String(128), nullable=False)  # agent_id, config_id, device_mac
    interface_name = Column(String(128), nullable=False)
    rx_bytes       = Column(BigInteger, default=0)
    tx_bytes       = Column(BigInteger, default=0)
    rx_rate_bps    = Column(BigInteger, default=0)        # calculated rate in bits/sec
    tx_rate_bps    = Column(BigInteger, default=0)

    __table_args__ = (
        Index("ix_bw_source_ts", "source_type", "source_id", "timestamp"),
        Index("ix_bw_source_iface", "source_type", "source_id", "interface_name"),
    )
