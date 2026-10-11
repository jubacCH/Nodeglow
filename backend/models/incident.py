"""Incident and IncidentEvent models for the correlation engine."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from models.base import Base
from models.tenant import TenantScoped, tenant_fk, tenant_parent_unique


class Incident(TenantScoped, Base):
    __tablename__ = "incidents"
    __tenant_index__ = False  # covered by uq_incidents_tenant_id_id

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule = Column(String(64), nullable=False)            # rule that created it
    title = Column(String(256), nullable=False)
    severity = Column(String(16), nullable=False, default="warning")  # critical | warning | info
    status = Column(String(16), nullable=False, default="open")       # open | acknowledged | resolved
    host_ids_hash = Column(String(64), nullable=True)    # for dedup: hash of sorted affected host IDs
    # The affected ping host ids themselves, as a sorted JSON list ("[3,7]").
    # NULL = not recorded (incidents from before revision 037, or rules that
    # are not about hosts) — "unknown", never "no hosts". "[]" = recorded, none.
    host_ids = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(128), nullable=True)
    postmortem = Column(Text, nullable=True)
    postmortem_generated_at = Column(DateTime, nullable=True)

    # ── Operator feedback loop (Phase 3) ───────────────────────────────────
    feedback = Column(String(16), nullable=True)          # 'real' | 'noise' | NULL
    feedback_at = Column(DateTime, nullable=True)
    feedback_by = Column(String(128), nullable=True)
    # Precursor template text for learned_precursor incidents, so 'noise'
    # feedback can map the incident back to the noisy template and blacklist it.
    precursor_template = Column(Text, nullable=True)

    events = relationship("IncidentEvent", back_populates="incident",
                          cascade="all, delete-orphan", order_by="IncidentEvent.timestamp")

    __table_args__ = (
        Index("ix_incident_status", "tenant_id", "status"),
        Index("ix_incident_rule_hash", "tenant_id", "rule", "host_ids_hash"),
        Index("ix_incident_updated", "updated_at"),
        Index("ix_incident_feedback", "feedback"),
        tenant_parent_unique("incidents"),
    )


class IncidentEvent(TenantScoped, Base):
    __tablename__ = "incident_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    event_type = Column(String(32), nullable=False)  # created | host_down | host_up | syslog_error | integration_error | resolved | acknowledged
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)              # extra JSON or text

    incident = relationship("Incident", back_populates="events")

    __table_args__ = (
        Index("ix_incident_event_ts", "incident_id", timestamp.desc()),
        Index("ix_incident_event_type_ts", "incident_id", "event_type", timestamp.desc()),
        tenant_fk("incident_id", "incidents", name="fk_incident_events_incident"),
    )
