"""Recurring and one-off maintenance windows.

A window puts hosts into maintenance for a period without anyone having to
flip the per-host flag by hand. Whether a window is active right now is
computed (services/maintenance.py), never stored, so a window can never be
"stuck on" the way a forgotten flag can.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from models.base import Base


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    name             = Column(String(128), nullable=False)
    enabled          = Column(Boolean, nullable=False, default=True)
    # "weekly": weekdays + start_time + duration_minutes, in `timezone`.
    # "once":   starts_at .. ends_at (naive UTC, like every other column).
    kind             = Column(String(16), nullable=False, default="weekly")
    weekdays         = Column(String(32), nullable=True)   # "0,2,4" — 0 = Monday
    start_time       = Column(String(5), nullable=True)    # "HH:MM", local to `timezone`
    duration_minutes = Column(Integer, nullable=True)
    starts_at        = Column(DateTime, nullable=True)
    ends_at          = Column(DateTime, nullable=True)
    timezone         = Column(String(64), nullable=False, default="UTC")
    # Scope: every host, or the explicit ids in host_ids (JSON list).
    all_hosts        = Column(Boolean, nullable=False, default=False)
    host_ids         = Column(Text, nullable=True)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=True, default=datetime.utcnow,
                              onupdate=datetime.utcnow)
