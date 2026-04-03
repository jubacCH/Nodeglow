"""
Bandwidth service — extract traffic data from agent/integration snapshots,
calculate rates, and provide query helpers for the bandwidth API.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.bandwidth import BandwidthSample

logger = logging.getLogger(__name__)


# ── Extraction helpers ──────────────────────────────────────────────────────


def _calc_rate_bps(
    prev_bytes: int | None,
    curr_bytes: int | None,
    delta_seconds: float,
) -> int:
    """Calculate bits-per-second rate from byte counter delta.

    Returns 0 if calculation is impossible (no previous sample, counter reset,
    or non-positive time delta).
    """
    if prev_bytes is None or curr_bytes is None or delta_seconds <= 0:
        return 0
    delta = curr_bytes - prev_bytes
    if delta < 0:
        # Counter reset (e.g. reboot) — skip this sample
        return 0
    return int((delta * 8) / delta_seconds)


async def _get_previous_sample(
    db: AsyncSession,
    source_type: str,
    source_id: str,
    interface_name: str,
) -> BandwidthSample | None:
    """Get the most recent sample for a specific source+interface."""
    result = await db.execute(
        select(BandwidthSample)
        .where(
            BandwidthSample.source_type == source_type,
            BandwidthSample.source_id == source_id,
            BandwidthSample.interface_name == interface_name,
        )
        .order_by(BandwidthSample.timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Agent bandwidth extraction ──────────────────────────────────────────────


async def extract_agent_bandwidth(
    db: AsyncSession,
    agent_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
) -> int:
    """Extract bandwidth samples from an agent snapshot.

    Parses network_interfaces from the agent's data_json, calculates rates
    from previous samples, and inserts BandwidthSample records.

    Returns the number of samples inserted.
    """
    if snapshot_data_json is None:
        return 0

    if isinstance(snapshot_data_json, str):
        try:
            data = json.loads(snapshot_data_json)
        except (json.JSONDecodeError, TypeError):
            return 0
    else:
        data = snapshot_data_json

    interfaces = data.get("network_interfaces", [])
    if not interfaces:
        # Fallback: check top-level network dict (older agent format)
        net = data.get("network", {})
        if net and ("rx_bytes" in net or "tx_bytes" in net):
            interfaces = [{"name": "total", **net}]

    if not interfaces:
        return 0

    ts = timestamp or datetime.utcnow()
    source_id = str(agent_id)
    count = 0

    for iface in interfaces:
        iface_name = iface.get("name") or iface.get("interface", "unknown")
        rx = iface.get("rx_bytes")
        tx = iface.get("tx_bytes")

        if rx is None and tx is None:
            continue

        rx = int(rx) if rx is not None else 0
        tx = int(tx) if tx is not None else 0

        # Get previous sample for rate calculation
        prev = await _get_previous_sample(db, "agent", source_id, iface_name)
        rx_rate = 0
        tx_rate = 0
        if prev and prev.timestamp:
            delta_s = (ts - prev.timestamp).total_seconds()
            rx_rate = _calc_rate_bps(prev.rx_bytes, rx, delta_s)
            tx_rate = _calc_rate_bps(prev.tx_bytes, tx, delta_s)

        sample = BandwidthSample(
            timestamp=ts,
            source_type="agent",
            source_id=source_id,
            interface_name=iface_name,
            rx_bytes=rx,
            tx_bytes=tx,
            rx_rate_bps=rx_rate,
            tx_rate_bps=tx_rate,
        )
        db.add(sample)
        count += 1

    if count:
        await db.flush()
    return count


# ── Proxmox bandwidth extraction ───────────────────────────────────────────


async def extract_proxmox_bandwidth(
    db: AsyncSession,
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
) -> int:
    """Extract bandwidth samples from a Proxmox integration snapshot.

    Parses netin/netout from nodes and VMs in the snapshot data.
    Returns the number of samples inserted.
    """
    if snapshot_data_json is None:
        return 0

    if isinstance(snapshot_data_json, str):
        try:
            data = json.loads(snapshot_data_json)
        except (json.JSONDecodeError, TypeError):
            return 0
    else:
        data = snapshot_data_json

    ts = timestamp or datetime.utcnow()
    source_id = str(config_id)
    count = 0

    # Proxmox data may contain nodes and VMs/CTs
    nodes = data.get("nodes", [])
    for node in nodes:
        node_name = node.get("node", node.get("name", "unknown"))
        netin = node.get("netin")
        netout = node.get("netout")

        if netin is not None or netout is not None:
            rx = int(netin) if netin is not None else 0
            tx = int(netout) if netout is not None else 0
            iface_name = f"node/{node_name}"

            prev = await _get_previous_sample(db, "proxmox", source_id, iface_name)
            rx_rate = 0
            tx_rate = 0
            if prev and prev.timestamp:
                delta_s = (ts - prev.timestamp).total_seconds()
                rx_rate = _calc_rate_bps(prev.rx_bytes, rx, delta_s)
                tx_rate = _calc_rate_bps(prev.tx_bytes, tx, delta_s)

            db.add(BandwidthSample(
                timestamp=ts,
                source_type="proxmox",
                source_id=source_id,
                interface_name=iface_name,
                rx_bytes=rx,
                tx_bytes=tx,
                rx_rate_bps=rx_rate,
                tx_rate_bps=tx_rate,
            ))
            count += 1

    # VMs and containers (qemu + lxc)
    for vm_type in ("qemu", "lxc"):
        vms = data.get(vm_type, [])
        for vm in vms:
            vmid = vm.get("vmid", vm.get("id", "?"))
            vm_name = vm.get("name", f"{vm_type}-{vmid}")
            netin = vm.get("netin")
            netout = vm.get("netout")

            if netin is not None or netout is not None:
                rx = int(netin) if netin is not None else 0
                tx = int(netout) if netout is not None else 0
                iface_name = f"{vm_type}/{vmid}-{vm_name}"

                prev = await _get_previous_sample(db, "proxmox", source_id, iface_name)
                rx_rate = 0
                tx_rate = 0
                if prev and prev.timestamp:
                    delta_s = (ts - prev.timestamp).total_seconds()
                    rx_rate = _calc_rate_bps(prev.rx_bytes, rx, delta_s)
                    tx_rate = _calc_rate_bps(prev.tx_bytes, tx, delta_s)

                db.add(BandwidthSample(
                    timestamp=ts,
                    source_type="proxmox",
                    source_id=source_id,
                    interface_name=iface_name,
                    rx_bytes=rx,
                    tx_bytes=tx,
                    rx_rate_bps=rx_rate,
                    tx_rate_bps=tx_rate,
                ))
                count += 1

    if count:
        await db.flush()
    return count


# ── UniFi bandwidth extraction ─────────────────────────────────────────────


async def extract_unifi_bandwidth(
    db: AsyncSession,
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
) -> int:
    """Extract bandwidth samples from a UniFi integration snapshot.

    UniFi provides rx_bytes_r/tx_bytes_r which are already rates (bytes/sec),
    so we convert to bits/sec directly without needing a previous sample.
    Returns the number of samples inserted.
    """
    if snapshot_data_json is None:
        return 0

    if isinstance(snapshot_data_json, str):
        try:
            data = json.loads(snapshot_data_json)
        except (json.JSONDecodeError, TypeError):
            return 0
    else:
        data = snapshot_data_json

    ts = timestamp or datetime.utcnow()
    source_id = str(config_id)
    count = 0

    devices = data.get("devices", [])
    for device in devices:
        dev_mac = device.get("mac", device.get("_id", "unknown"))
        dev_name = device.get("name", dev_mac)

        # Device-level rates
        rx_rate = device.get("rx_bytes_r") or device.get("rx_bytes-r")
        tx_rate = device.get("tx_bytes_r") or device.get("tx_bytes-r")

        if rx_rate is not None or tx_rate is not None:
            rx_bps = int(rx_rate or 0) * 8  # bytes/s -> bits/s
            tx_bps = int(tx_rate or 0) * 8

            db.add(BandwidthSample(
                timestamp=ts,
                source_type="unifi",
                source_id=source_id,
                interface_name=f"device/{dev_name}",
                rx_bytes=0,  # UniFi rates don't give cumulative bytes
                tx_bytes=0,
                rx_rate_bps=rx_bps,
                tx_rate_bps=tx_bps,
            ))
            count += 1

        # Per-port stats (for switches)
        ports = device.get("port_table", [])
        for port in ports:
            port_idx = port.get("port_idx", port.get("name", "?"))
            port_name = port.get("name", f"port{port_idx}")
            p_rx = port.get("rx_bytes_r") or port.get("rx_bytes-r")
            p_tx = port.get("tx_bytes_r") or port.get("tx_bytes-r")

            if p_rx is not None or p_tx is not None:
                db.add(BandwidthSample(
                    timestamp=ts,
                    source_type="unifi",
                    source_id=source_id,
                    interface_name=f"device/{dev_name}/{port_name}",
                    rx_bytes=0,
                    tx_bytes=0,
                    rx_rate_bps=int(p_rx or 0) * 8,
                    tx_rate_bps=int(p_tx or 0) * 8,
                ))
                count += 1

    if count:
        await db.flush()
    return count


# ── Query helpers ───────────────────────────────────────────────────────────


async def get_bandwidth_history(
    db: AsyncSession,
    source_type: str | None = None,
    source_id: str | None = None,
    interface_name: str | None = None,
    hours: int = 24,
    limit: int = 2000,
) -> list[dict]:
    """Return time-series bandwidth data for charting.

    Filters by source_type, source_id, and/or interface_name.
    Returns list of dicts sorted by timestamp ascending.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    q = select(BandwidthSample).where(BandwidthSample.timestamp >= cutoff)

    if source_type:
        q = q.where(BandwidthSample.source_type == source_type)
    if source_id:
        q = q.where(BandwidthSample.source_id == source_id)
    if interface_name:
        q = q.where(BandwidthSample.interface_name == interface_name)

    q = q.order_by(BandwidthSample.timestamp.asc()).limit(limit)

    result = await db.execute(q)
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "source_type": r.source_type,
            "source_id": r.source_id,
            "interface_name": r.interface_name,
            "rx_bytes": r.rx_bytes,
            "tx_bytes": r.tx_bytes,
            "rx_rate_bps": r.rx_rate_bps,
            "tx_rate_bps": r.tx_rate_bps,
        }
        for r in rows
    ]


async def get_bandwidth_summary(db: AsyncSession) -> dict:
    """Return bandwidth summary: top talkers and total throughput.

    Top talkers are based on the most recent sample per source+interface,
    ranked by combined rx+tx rate.
    """
    # Get the latest sample for each source+interface (last hour only)
    cutoff = datetime.utcnow() - timedelta(hours=1)

    # Subquery: max id per (source_type, source_id, interface_name)
    sub = (
        select(
            func.max(BandwidthSample.id).label("max_id"),
        )
        .where(BandwidthSample.timestamp >= cutoff)
        .group_by(
            BandwidthSample.source_type,
            BandwidthSample.source_id,
            BandwidthSample.interface_name,
        )
        .subquery()
    )

    result = await db.execute(
        select(BandwidthSample).join(sub, BandwidthSample.id == sub.c.max_id)
    )
    latest = result.scalars().all()

    # Sort by total rate descending for top talkers
    sorted_samples = sorted(
        latest,
        key=lambda s: (s.rx_rate_bps or 0) + (s.tx_rate_bps or 0),
        reverse=True,
    )

    total_rx_bps = sum(s.rx_rate_bps or 0 for s in latest)
    total_tx_bps = sum(s.tx_rate_bps or 0 for s in latest)

    # Per-source aggregates
    by_source: dict[str, dict] = {}
    for s in latest:
        key = f"{s.source_type}:{s.source_id}"
        if key not in by_source:
            by_source[key] = {
                "source_type": s.source_type,
                "source_id": s.source_id,
                "rx_rate_bps": 0,
                "tx_rate_bps": 0,
                "interface_count": 0,
            }
        by_source[key]["rx_rate_bps"] += s.rx_rate_bps or 0
        by_source[key]["tx_rate_bps"] += s.tx_rate_bps or 0
        by_source[key]["interface_count"] += 1

    # Resolve names for display
    agent_names: dict[str, str] = {}
    config_names: dict[str, str] = {}
    try:
        from models.agent import Agent
        agents = (await db.execute(select(Agent.id, Agent.hostname))).all()
        agent_names = {str(a.id): a.hostname for a in agents}
    except Exception:
        pass
    try:
        from models.integration import IntegrationConfig
        configs = (await db.execute(
            select(IntegrationConfig.id, IntegrationConfig.name, IntegrationConfig.type)
        )).all()
        config_names = {str(c.id): c.name for c in configs}
    except Exception:
        pass

    def _label(s: BandwidthSample) -> str:
        if s.source_type == "agent":
            return agent_names.get(s.source_id, s.source_id)
        return config_names.get(s.source_id, s.source_id)

    def _iface_label(s: BandwidthSample) -> str:
        name = s.interface_name
        # Clean up "device/..." prefix for UniFi
        if name.startswith("device/"):
            name = name[7:]
        return name

    return {
        "total_rx_bps": total_rx_bps,
        "total_tx_bps": total_tx_bps,
        "total_interfaces": len(latest),
        "top_talkers": [
            {
                "source_type": s.source_type,
                "source_id": s.source_id,
                "source_name": _label(s),
                "interface_name": _iface_label(s),
                "rx_rate_bps": s.rx_rate_bps or 0,
                "tx_rate_bps": s.tx_rate_bps or 0,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in sorted_samples[:20]
        ],
        "by_source": list(by_source.values()),
    }


async def get_bandwidth_interfaces(db: AsyncSession) -> list[dict]:
    """List all known interfaces with their latest rates (last hour only)."""
    cutoff = datetime.utcnow() - timedelta(hours=1)

    sub = (
        select(
            func.max(BandwidthSample.id).label("max_id"),
        )
        .where(BandwidthSample.timestamp >= cutoff)
        .group_by(
            BandwidthSample.source_type,
            BandwidthSample.source_id,
            BandwidthSample.interface_name,
        )
        .subquery()
    )

    result = await db.execute(
        select(BandwidthSample).join(sub, BandwidthSample.id == sub.c.max_id)
    )
    latest = result.scalars().all()

    # Resolve agent hostnames
    agent_names: dict[str, str] = {}
    try:
        from models.agent import Agent
        agents = (await db.execute(select(Agent.id, Agent.hostname))).all()
        agent_names = {str(a.id): a.hostname for a in agents}
    except Exception:
        pass

    # Resolve integration config names
    config_names: dict[str, str] = {}
    try:
        from models.integration import IntegrationConfig
        configs = (await db.execute(
            select(IntegrationConfig.id, IntegrationConfig.name, IntegrationConfig.type)
        )).all()
        config_names = {str(c.id): f"{c.type}/{c.name}" for c in configs}
    except Exception:
        pass

    def _display_name(s: BandwidthSample) -> str:
        if s.source_type == "agent":
            host = agent_names.get(s.source_id, s.source_id)
            iface = s.interface_name.replace("total", "all")
            return f"{host} ({iface})"
        elif s.source_type in ("unifi", "proxmox"):
            src = config_names.get(s.source_id, s.source_type)
            return f"{src} / {s.interface_name}"
        return f"{s.source_type}/{s.interface_name}"

    return [
        {
            "source_type": s.source_type,
            "source_id": s.source_id,
            "interface_name": s.interface_name,
            "display_name": _display_name(s),
            "rx_rate_bps": s.rx_rate_bps or 0,
            "tx_rate_bps": s.tx_rate_bps or 0,
            "rx_bytes": s.rx_bytes or 0,
            "tx_bytes": s.tx_bytes or 0,
            "last_seen": s.timestamp.isoformat(),
        }
        for s in sorted(
            latest,
            key=lambda s: (s.rx_rate_bps or 0) + (s.tx_rate_bps or 0),
            reverse=True,
        )
    ]


# ── Cleanup ─────────────────────────────────────────────────────────────────


async def cleanup_old_bandwidth(db: AsyncSession, days: int = 7) -> int:
    """Delete bandwidth samples older than the retention period.

    Returns the number of rows deleted.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        delete(BandwidthSample).where(BandwidthSample.timestamp < cutoff)
    )
    return result.rowcount or 0
