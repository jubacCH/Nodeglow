"""SNMP models – MIBs, OIDs, host configs, and metric results."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index,
    Integer, String, Text,
)

from models.base import Base


class SnmpMib(Base):
    """Uploaded MIB module with parsed OID definitions."""
    __tablename__ = "snmp_mibs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(128), nullable=False, unique=True)  # MIB module name
    filename    = Column(String(256))
    oid_count   = Column(Integer, default=0)
    raw_text    = Column(Text)           # original MIB source (for re-parsing)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class SnmpOid(Base):
    """Resolved OID → name mapping from MIB files."""
    __tablename__ = "snmp_oids"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    oid         = Column(String(256), nullable=False, unique=True)   # 1.3.6.1.2.1.1.1
    name        = Column(String(256), nullable=False)                # sysDescr
    mib_name    = Column(String(128), index=True)                    # SNMPv2-MIB
    syntax      = Column(String(64))                                 # DisplayString, Integer32
    description = Column(Text)
    is_table    = Column(Boolean, default=False)                     # table entry (indexed)


class SnmpHostConfig(Base):
    """Per-host SNMP configuration – links a PingHost to credentials + OIDs to poll."""
    __tablename__ = "snmp_host_configs"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    host_id       = Column(Integer, ForeignKey("ping_hosts.id", ondelete="CASCADE"),
                           nullable=False, unique=True)
    credential_id = Column(Integer, ForeignKey("credentials.id", ondelete="SET NULL"),
                           nullable=True)
    port          = Column(Integer, default=161)
    oids_json       = Column(Text)    # JSON list of OIDs to poll (or null = use defaults)
    thresholds_json = Column(Text)    # JSON {oid_name: {warn: N, crit: N, op: ">"|"<"}, ...}
    poll_interval   = Column(Integer, default=60)  # seconds
    enabled       = Column(Boolean, default=True)
    last_poll     = Column(DateTime, nullable=True)
    last_ok       = Column(Boolean, nullable=True)


class SnmpResult(Base):
    """Time-series SNMP metric data per host."""
    __tablename__ = "snmp_results"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    host_id   = Column(Integer, ForeignKey("ping_hosts.id", ondelete="CASCADE"),
                       nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    data_json = Column(Text)     # {oid: value, ...} or {name: value, ...}

    __table_args__ = (
        Index("ix_snmp_results_host_ts", "host_id", timestamp.desc()),
    )
