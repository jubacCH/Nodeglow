"""Generic IntegrationConfig and Snapshot models for all integrations."""
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Index, Integer, String, Text, text,
)

from models.base import Base
from models.tenant import TenantScoped, tenant_fk

# Who collects a config (probe-side integrations, revision 048).
COLLECTOR_CORE = "core"
COLLECTOR_PROBE = "probe"
COLLECTORS = (COLLECTOR_CORE, COLLECTOR_PROBE)


class IntegrationConfig(TenantScoped, Base):
    """
    Single table for all integration configurations.
    Each row stores one integration instance (e.g. one Proxmox cluster, one UniFi controller).
    The config_json field holds all config fields as encrypted JSON.

    cluster_group: optional logical grouping. Multiple integrations sharing
    the same cluster_group are treated as members of one HA source — the
    scheduler picks one as the active primary each cycle, the others poll
    for failover but skip duplicate writes (snapshots, bandwidth, alerts).
    For Proxmox integrations this is auto-populated from the cluster_name
    after the first successful poll.

    collector / probe_id / config_rev (revision 048, probe-side integrations):
    ``collector`` names who collects the config — the core (``'core'``, every
    existing row) or a probe (``'probe'``, with ``probe_id``). Who collects is
    the column, never the nullness of ``probe_id``: deleting a probe clears
    ``probe_id`` (SET NULL) but leaves ``collector = 'probe'`` — "needs a
    probe" — and the core still does not collect it. ``config_rev`` is bumped
    on every change of the config; a result is stored only if it was started
    with the config's current ``config_rev`` and collector
    (``services.integration_ingest``).
    """
    __tablename__ = "integration_configs"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    type          = Column(String(32), nullable=False, index=True)
    name          = Column(String(128), nullable=False)
    config_json   = Column(Text, nullable=False)    # encrypted JSON with all fields
    enabled       = Column(Boolean, default=True)
    cluster_group = Column(String(128), nullable=True, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    collector     = Column(String(16), nullable=False, default=COLLECTOR_CORE,
                           server_default=COLLECTOR_CORE)
    probe_id      = Column(Integer, nullable=True, index=True)
    config_rev    = Column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("collector IN ('core', 'probe')",
                        name="ck_integration_configs_collector"),
        CheckConstraint("collector = 'probe' OR probe_id IS NULL",
                        name="ck_integration_configs_probe_collector"),
        # The probe belongs to the config's own tenant; removing it clears
        # only probe_id, the config stays a probe config ("needs a probe").
        tenant_fk("probe_id", "agents", name="fk_integration_configs_probe_id",
                  ondelete="SET NULL"),
    )


class Snapshot(TenantScoped, Base):
    """
    Generic snapshot table for all integration types.
    Stores the latest collected data as JSON.
    """
    __tablename__ = "snapshots"
    __tenant_index__ = False  # the composite indexes below lead with tenant_id
    id          = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String(32), nullable=False)
    entity_id   = Column(Integer, nullable=False)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    ok          = Column(Boolean, nullable=False, default=True)
    data_json   = Column(Text, nullable=True)
    error       = Column(Text, nullable=True)
    # Revision 048: the probe that collected this snapshot (NULL = the core;
    # a soft reference, no foreign key — history outlives a deleted probe)
    # and the probe's collection time, clock-corrected. ``timestamp`` stays
    # the authoritative "as of" time and is set from collected_at.
    collected_by = Column(Integer, nullable=True)
    collected_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_snap_type_entity_ts", "tenant_id", "entity_type", "entity_id", timestamp.desc()),
        Index("ix_snap_entity_id", "entity_id"),
        Index("ix_snap_type_ts", "tenant_id", "entity_type", "timestamp"),
        # Precursor learning filters on ok/timestamp only. Partial, so it holds
        # just the handful of failed snapshots instead of the whole table.
        Index("ix_snap_failed_ts", "timestamp", postgresql_where=text("ok = false")),
        # Serves max(id) GROUP BY (entity_type, entity_id) — the "newest
        # snapshot per entity" lookup — from the index instead of the heap.
        Index("ix_snap_latest_per_entity", "tenant_id", "entity_type", "entity_id", id.desc()),
    )
