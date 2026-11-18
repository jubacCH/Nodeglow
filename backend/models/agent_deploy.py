"""Agent deploy over SSH: pinned host keys and per-target deploy jobs.

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``. Neither table holds
a secret: credentials live only in the memory of the running deploy task, and
job output has every issued install token reduced to its prefix.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from models.base import Base
from models.tenant import TenantScoped


class SshHostKey(TenantScoped, Base):
    """The host key an admin confirmed for ``host:port``. A connection to that
    address accepts this key only."""

    __tablename__ = "ssh_host_keys"
    __tenant_index__ = False  # covered by uq_ssh_host_keys_address

    id = Column(Integer, primary_key=True, autoincrement=True)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=22)
    key_type = Column(String(64), nullable=False)
    public_key = Column(Text, nullable=False)        # OpenSSH format
    fingerprint = Column(String(128), nullable=False)  # SHA256:...
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "host", "port", name="uq_ssh_host_keys_address"),
    )


class AgentDeployJob(TenantScoped, Base):
    """One target of a deploy run: a host over SSH, or a container on a
    Proxmox node. ``status`` is queued | running | ok | failed."""

    __tablename__ = "agent_deploy_jobs"
    __tenant_index__ = False  # covered by ix_agent_deploy_jobs_run

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False)        # ssh | proxmox_lxc
    target = Column(String(255), nullable=False)
    agent_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="queued")
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_agent_deploy_jobs_run", "tenant_id", "run_id"),
    )
