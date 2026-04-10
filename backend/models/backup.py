"""Backup monitoring models – BackupJob and BackupHistory."""

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index,
    Integer, String, Text, func,
)

from models.base import Base


class BackupJob(Base):
    """Tracks a single backup job from any source (Proxmox, UNAS, TrueNAS, etc.)."""
    __tablename__ = "backup_jobs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    name            = Column(String(256), nullable=False)
    source_type     = Column(String(32), nullable=False, index=True)   # "proxmox", "unas", "truenas"
    source_config_id = Column(Integer, nullable=True)                  # FK to integration_configs.id (soft ref)
    target_name     = Column(String(256), nullable=False)              # VM name, dataset, etc
    target_vmid     = Column(Integer, nullable=True)                   # Proxmox VMID if applicable
    storage_name    = Column(String(128), nullable=True)

    last_run_at     = Column(DateTime, nullable=True)
    last_status     = Column(String(32), default="unknown")            # ok, failed, running, warning, unknown
    last_duration_sec = Column(Integer, nullable=True)
    last_size_bytes = Column(BigInteger, nullable=True)
    last_error      = Column(Text, nullable=True)

    expected_frequency_hours = Column(Integer, default=24)
    enabled         = Column(Boolean, default=True)

    created_at      = Column(DateTime, default=func.now())
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_backup_jobs_source", "source_type", "source_config_id"),
    )


class BackupHistory(Base):
    """Individual backup run records linked to a BackupJob."""
    __tablename__ = "backup_history"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    job_id      = Column(Integer, ForeignKey("backup_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp   = Column(DateTime, nullable=False, index=True)
    status      = Column(String(32), nullable=False)
    duration_sec = Column(Integer, nullable=True)
    size_bytes  = Column(BigInteger, nullable=True)
    error       = Column(Text, nullable=True)
    details_json = Column(Text, nullable=True)  # extra metadata as JSON

    __table_args__ = (
        Index("ix_backup_history_job_ts", "job_id", timestamp.desc()),
    )
