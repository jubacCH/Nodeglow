"""Agent deploy over SSH: pinned host keys and per-target deploy jobs.

See ``docs/specs/2026-11-18-agent-ssh-deploy-design.md``. Neither table holds
a secret: credentials live only in the memory of the running deploy task, and
job output has every issued install token reduced to its prefix.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func

from models.base import Base
from models.tenant import TenantScoped, tenant_fk


class SshHostKey(TenantScoped, Base):
    """The host key an admin confirmed for ``host:port`` as seen from the
    server (``probe_id`` NULL) or from a probe. A connection to that address
    accepts this key only."""

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
    probe_id = Column(Integer, nullable=True)

    __table_args__ = (
        # One key per address and viewpoint; NULL (the server) folds to 0.
        Index("uq_ssh_host_keys_address", "tenant_id", func.coalesce(probe_id, 0), host, port,
              unique=True),
        tenant_fk("probe_id", "agents", name="fk_ssh_host_keys_probe", ondelete="CASCADE"),
    )


class AgentDeployJob(TenantScoped, Base):
    """One target of a deploy run: a host over SSH, or a container on a
    Proxmox node. ``status`` is queued | running | ok | failed."""

    __tablename__ = "agent_deploy_jobs"
    __tenant_index__ = False  # covered by ix_agent_deploy_jobs_run

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False)        # ssh | proxmox_lxc | probe_host | probe_lxc
    target = Column(String(255), nullable=False)
    agent_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="queued")
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)
    # Through a probe (part C): the probe and the command that carries the job.
    probe_id = Column(Integer, nullable=True)
    command_id = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_agent_deploy_jobs_run", "tenant_id", "run_id"),
    )


class AgentCommand(TenantScoped, Base):
    """A structured command for an agent (part C: a probe), delivered with its
    heartbeat. ``payload`` is encrypted under the tenant key and cleared once
    delivered; ``result`` is what the agent answered, redacted.
    ``status``: queued | delivered | ok | failed | expired."""

    __tablename__ = "agent_commands"
    __tenant_index__ = False  # covered by ix_agent_commands_agent

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=False)
    op = Column(String(32), nullable=False)
    payload = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="queued")
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_agent_commands_agent", "tenant_id", "agent_id", "status"),
        tenant_fk("agent_id", "agents", name="fk_agent_commands_agent", ondelete="CASCADE"),
    )
