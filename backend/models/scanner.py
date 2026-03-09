"""Scheduled subnet scan model and scan log."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from models.base import Base


class SubnetScanSchedule(Base):
    __tablename__ = "subnet_scan_schedules"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(128), nullable=False)
    cidr        = Column(String(64), nullable=False)
    interval_m  = Column(Integer, default=60)           # scan interval in minutes
    auto_add    = Column(Boolean, default=True)          # auto-add discovered hosts
    enabled     = Column(Boolean, default=True)
    last_scan   = Column(DateTime, nullable=True)
    last_alive  = Column(Integer, nullable=True)         # alive count from last scan
    last_total  = Column(Integer, nullable=True)         # total count from last scan
    last_added  = Column(Integer, default=0)             # hosts added in last scan
    created_at  = Column(DateTime, default=datetime.utcnow)


class SubnetScanLog(Base):
    """Log entry for each scheduled scan run, including which hosts were added."""
    __tablename__ = "subnet_scan_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey("subnet_scan_schedules.id", ondelete="CASCADE"), nullable=False)
    timestamp   = Column(DateTime, nullable=False, default=datetime.utcnow)
    cidr        = Column(String(64), nullable=False)
    alive       = Column(Integer, nullable=False, default=0)
    total       = Column(Integer, nullable=False, default=0)
    added       = Column(Integer, nullable=False, default=0)
    hosts_added = Column(Text, nullable=True)    # JSON list of added host names/IPs
    error       = Column(Text, nullable=True)     # error message if scan failed

    __table_args__ = (
        Index("ix_scan_log_schedule_ts", "schedule_id", timestamp.desc()),
    )
