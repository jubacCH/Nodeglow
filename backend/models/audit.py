"""Audit log model — tracks user actions for accountability."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from models.base import Base
from models.tenant import TenantScoped


class AuditLog(TenantScoped, Base):
    __tablename__ = "audit_logs"
    # NULL = installation-level entry (no tenant), visible only in system scope.
    __tenant_nullable__ = True
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
    # The provider of the tenant (or of a provider-level action); NULL for
    # installation-level entries.
    provider_id = Column(Integer, ForeignKey("providers.id", name="fk_audit_logs_provider"),
                         nullable=True)
    # How the actor had access: membership | provider_role | operator |
    # api_key | agent | system (see services.audit).
    acting_via  = Column(String(16), nullable=True)
