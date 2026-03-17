"""Audit log model — tracks user actions for accountability."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime, default=datetime.utcnow, index=True)
    user_id     = Column(Integer, nullable=True)
    username    = Column(String(64), nullable=True)
    action      = Column(String(64), nullable=False)    # login, create, update, delete
    target_type = Column(String(64), nullable=True)     # host, integration, rule, user, setting, agent
    target_id   = Column(Integer, nullable=True)
    target_name = Column(String(256), nullable=True)
    details     = Column(Text, nullable=True)            # JSON
    ip_address  = Column(String(45), nullable=True)
