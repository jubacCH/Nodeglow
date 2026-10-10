"""Per-user preferences (IA B-10), starting with "last dashboard visit".

One row per user, created on first write. Kept apart from ``users`` so that
preferences can grow (time zone, density, saved layout) without touching the
authentication table, and so a row can be missing — every reader must treat a
missing row as "no preference recorded yet".
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer

from models.base import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # When the user last acknowledged the dashboard ("seen"), naive UTC. The
    # dashboard's "since your last visit" counts everything after it.
    dashboard_seen_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
