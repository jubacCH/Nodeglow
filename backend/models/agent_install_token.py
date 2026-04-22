"""Agent install token — single-purpose credential for agent enrollment.

Replaces the legacy shared `agent_enrollment_key` global setting. Each token
is created by an admin, has an expiry, and can optionally be scoped to a
hostname pattern. Multi-use by design (one token per rollout batch); the
admin can revoke or set a short expiry for tighter control.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from models.base import Base


class AgentInstallToken(Base):
    """One-off enrollment credential issued to provision an agent."""
    __tablename__ = "agent_install_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256(SECRET_KEY, raw_token) — raw value is shown once on creation.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    # First 8 chars of the raw token, for UI identification only.
    prefix = Column(String(8), nullable=False)
    # Human-readable note (e.g. "batch-2026-Q2-linux").
    note = Column(String(256), nullable=True)
    # Optional hostname match (exact or glob-free substring). Empty = any host.
    hostname_pattern = Column(String(256), nullable=True)
    # Hard expiry — enrollment refused after this moment.
    expires_at = Column(DateTime, nullable=False)
    # Admin can revoke without waiting for expiry.
    revoked = Column(Boolean, default=False, nullable=False)
    # Count of successful enrollments using this token (monitoring/audit only).
    used_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(64), nullable=True)
