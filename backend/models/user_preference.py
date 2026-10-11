"""Per-user preferences (IA B-10), starting with "last dashboard visit".

One row per user and tenant, created on first write: "since your last visit"
means the last visit to *this* tenant's dashboard. Kept apart from ``users``
so that preferences can grow (time zone, density, saved layout) without
touching the authentication table, and so a row can be missing — every reader
must treat a missing row as "no preference recorded yet".
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from models.base import Base
from models.tenant import TenantScoped


class UserPreference(TenantScoped, Base):
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Overrides the mixin's column: part of the primary key (user, tenant).
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", name="fk_user_preferences_tenant"),
        primary_key=True, index=True,
    )
    # When the user last acknowledged the dashboard ("seen"), naive UTC. The
    # dashboard's "since your last visit" counts everything after it.
    dashboard_seen_at = Column(DateTime, nullable=True)
    # The user's dashboard widget layout (JSON list), NULL = the default
    # layout. Was one installation-wide setting until revision 042.
    dashboard_layout = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)
