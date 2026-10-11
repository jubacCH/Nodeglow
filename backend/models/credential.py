"""Credential Store – encrypted credentials for SNMP, WinRM, SSH, etc."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from models.base import Base
from models.tenant import TenantScoped, tenant_parent_unique


class Credential(TenantScoped, Base):
    __tablename__ = "credentials"
    __tenant_index__ = False  # covered by uq_credentials_tenant_id_id
    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(128), nullable=False)
    type       = Column(String(32), nullable=False, index=True)  # snmp_v2c, snmp_v3, winrm, ssh
    data_json  = Column(Text, nullable=False)   # encrypted JSON with all fields
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (tenant_parent_unique("credentials"),)
