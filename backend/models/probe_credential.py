"""Deploy credentials sealed for a probe (part C, sealed credentials).

See ``docs/specs/2026-11-19-probe-sealed-credentials-design.md``. The secret
is encrypted in the browser to the probe's public key; the server stores that
envelope under the tenant key on top and never sees the plaintext.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from models.base import Base
from models.tenant import TenantScoped, tenant_fk


class ProbeCredential(TenantScoped, Base):
    """One named SSH credential a probe may use to deploy agents.
    ``auth`` is password | key; ``hosts`` is a JSON list of host patterns the
    credential is allowed for."""

    __tablename__ = "probe_credentials"
    __tenant_index__ = False  # covered by uq_probe_credentials_name

    id = Column(Integer, primary_key=True, autoincrement=True)
    probe_id = Column(Integer, nullable=False)
    name = Column(String(64), nullable=False)
    username = Column(String(255), nullable=False)
    auth = Column(String(16), nullable=False)
    hosts = Column(Text, nullable=False)
    key_fingerprint = Column(String(64), nullable=False)
    # Sealed envelope, encrypted again under the tenant key.
    sealed = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(255), nullable=True)
    last_used_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_probe_credentials_name", "tenant_id", "probe_id", "name", unique=True),
        tenant_fk("probe_id", "agents", name="fk_probe_credentials_probe", ondelete="CASCADE"),
    )
