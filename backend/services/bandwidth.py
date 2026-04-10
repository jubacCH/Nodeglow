"""
Bandwidth service — extract per-interface traffic data from agent and
integration snapshots and store it in ClickHouse `bandwidth_metrics`.

Post-cutover: ClickHouse is the only store. The previous Postgres-backed
implementation kept rows in `bandwidth_samples` for both writes and reads;
all of that has been replaced with `services.clickhouse_client` helpers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from services import clickhouse_client as ch

logger = logging.getLogger(__name__)


# ── Rate calculation ────────────────────────────────────────────────────────


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
        return 0  # Counter reset (e.g. reboot)
    return int((delta * 8) / delta_seconds)


async def _rate_from_history(
    source_type: str,
    source_id: str,
    interface_name: str,
    curr_rx: int,
    curr_tx: int,
    ts: datetime,
) -> tuple[int, int]:
    """Look up the previous CH sample and compute rx/tx rates."""
    try:
        prev = await ch.get_previous_bandwidth_sample(source_type, source_id, interface_name)
    except Exception as exc:
        logger.warning("ClickHouse history lookup failed: %s", exc)
        return 0, 0
    if not prev:
        return 0, 0
    prev_ts = prev.get("timestamp")
    if not isinstance(prev_ts, datetime):
        return 0, 0
    delta_s = (ts - prev_ts).total_seconds()
    return (
        _calc_rate_bps(prev.get("rx_bytes"), curr_rx, delta_s),
        _calc_rate_bps(prev.get("tx_bytes"), curr_tx, delta_s),
    )


# ── Agent bandwidth extraction ──────────────────────────────────────────────


async def extract_agent_bandwidth(
    db: Any,  # kept for signature compat; unused
    agent_id: int,
    body: dict,
    timestamp: datetime | None = None,
    *,
    source_name: str = "",
) -> int:
    """Extract bandwidth samples from an agent heartbeat payload.

    `db` is accepted but ignored — bandwidth lives in ClickHouse now.
    Returns the number of samples written.
    """
    if not body:
        return 0
    interfaces = body.get("network", {}).get("interfaces") or []
    if not interfaces:
        return 0

    ts = timestamp or datetime.utcnow()
    source_id = str(agent_id)
    rows: list[dict] = []

    for iface in interfaces:
        iface_name = iface.get("name") or iface.get("interface", "unknown")
        rx = iface.get("rx_bytes")
        tx = iface.get("tx_bytes")
        if rx is None and tx is None:
            continue
        rx = int(rx) if rx is not None else 0
        tx = int(tx) if tx is not None else 0

        rx_rate, tx_rate = await _rate_from_history("agent", source_id, iface_name, rx, tx, ts)
        rows.append({
            "timestamp": ts,
            "source_type": "agent",
            "source_id": source_id,
            "source_name": source_name,
            "interface_name": iface_name,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "rx_rate_bps": rx_rate,
            "tx_rate_bps": tx_rate,
        })

    if rows:
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (agent bandwidth) failed: %s", exc)
    return len(rows)


# ── Proxmox bandwidth extraction ───────────────────────────────────────────


async def extract_proxmox_bandwidth(
    db: Any,
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
    *,
    source_name: str = "",
) -> int:
    """Extract bandwidth samples from a Proxmox integration snapshot."""
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
    rows: list[dict] = []

    # Nodes
    for node in data.get("nodes", []):
        node_name = node.get("node", node.get("name", "unknown"))
        netin = node.get("netin")
        netout = node.get("netout")
        if netin is None and netout is None:
            continue
        rx = int(netin) if netin is not None else 0
        tx = int(netout) if netout is not None else 0
        iface_name = f"node/{node_name}"
        rx_rate, tx_rate = await _rate_from_history("proxmox", source_id, iface_name, rx, tx, ts)
        rows.append({
            "timestamp": ts,
            "source_type": "proxmox",
            "source_id": source_id,
            "source_name": source_name,
            "interface_name": iface_name,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "rx_rate_bps": rx_rate,
            "tx_rate_bps": tx_rate,
        })

    # VMs / containers
    for vm_type in ("qemu", "lxc"):
        for vm in data.get(vm_type, []):
            vmid = vm.get("vmid", vm.get("id", "?"))
            vm_name = vm.get("name", f"{vm_type}-{vmid}")
            netin = vm.get("netin")
            netout = vm.get("netout")
            if netin is None and netout is None:
                continue
            rx = int(netin) if netin is not None else 0
            tx = int(netout) if netout is not None else 0
            iface_name = f"{vm_type}/{vmid}-{vm_name}"
            rx_rate, tx_rate = await _rate_from_history("proxmox", source_id, iface_name, rx, tx, ts)
            rows.append({
                "timestamp": ts,
                "source_type": "proxmox",
                "source_id": source_id,
                "source_name": source_name,
                "interface_name": iface_name,
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_rate_bps": rx_rate,
                "tx_rate_bps": tx_rate,
            })

    if rows:
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (proxmox bandwidth) failed: %s", exc)
    return len(rows)


# ── UniFi bandwidth extraction ─────────────────────────────────────────────


async def extract_unifi_bandwidth(
    db: Any,
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
    *,
    source_name: str = "",
) -> int:
    """Extract bandwidth samples from a UniFi integration snapshot.

    UniFi exposes rx_bytes_r/tx_bytes_r as bytes/sec — convert to bits/sec
    directly without needing prior history.
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
    rows: list[dict] = []

    for device in data.get("devices", []):
        dev_mac = device.get("mac", device.get("_id", "unknown"))
        dev_name = device.get("name", dev_mac)

        rx_rate = device.get("rx_bytes_r") or device.get("rx_bytes-r")
        tx_rate = device.get("tx_bytes_r") or device.get("tx_bytes-r")
        if rx_rate is not None or tx_rate is not None:
            rows.append({
                "timestamp": ts,
                "source_type": "unifi",
                "source_id": source_id,
                "source_name": source_name,
                "interface_name": f"device/{dev_name}",
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_rate_bps": int(rx_rate or 0) * 8,
                "tx_rate_bps": int(tx_rate or 0) * 8,
            })

        for port in device.get("port_table", []) or []:
            port_idx = port.get("port_idx", port.get("name", "?"))
            port_name = port.get("name", f"port{port_idx}")
            p_rx = port.get("rx_bytes_r") or port.get("rx_bytes-r")
            p_tx = port.get("tx_bytes_r") or port.get("tx_bytes-r")
            if p_rx is None and p_tx is None:
                continue
            rows.append({
                "timestamp": ts,
                "source_type": "unifi",
                "source_id": source_id,
                "source_name": source_name,
                "interface_name": f"device/{dev_name}/{port_name}",
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_rate_bps": int(p_rx or 0) * 8,
                "tx_rate_bps": int(p_tx or 0) * 8,
            })

    if rows:
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (unifi bandwidth) failed: %s", exc)
    return len(rows)


# ── Query helpers (read from ClickHouse) ─────────────────────────────────────


async def get_bandwidth_history(
    db: Any = None,
    source_type: str | None = None,
    source_id: str | None = None,
    interface_name: str | None = None,
    hours: int = 24,
    limit: int = 2000,
) -> list[dict]:
    """Time-series bandwidth data for charting (sorted ascending)."""
    rows = await ch.get_bandwidth_history_ch(
        source_type=source_type,
        source_id=source_id,
        interface_name=interface_name,
        hours=hours,
        limit=limit,
    )
    return [
        {
            "timestamp": (
                r["timestamp"].isoformat() if isinstance(r["timestamp"], datetime) else r["timestamp"]
            ),
            "source_type": r["source_type"],
            "source_id": r["source_id"],
            "source_name": r.get("source_name", ""),
            "interface_name": r["interface_name"],
            "rx_bytes": int(r.get("rx_bytes") or 0),
            "tx_bytes": int(r.get("tx_bytes") or 0),
            "rx_rate_bps": int(r.get("rx_rate_bps") or 0),
            "tx_rate_bps": int(r.get("tx_rate_bps") or 0),
        }
        for r in rows
    ]


async def get_bandwidth_summary(db: Any = None) -> dict:
    """Top talkers + total throughput, computed from latest sample per
    (source_type, source_id, interface_name) over the last hour."""
    latest = await ch.get_latest_bandwidth_per_iface(since_hours=1)

    sorted_samples = sorted(
        latest,
        key=lambda r: int(r.get("rx_rate_bps") or 0) + int(r.get("tx_rate_bps") or 0),
        reverse=True,
    )
    total_rx_bps = sum(int(r.get("rx_rate_bps") or 0) for r in latest)
    total_tx_bps = sum(int(r.get("tx_rate_bps") or 0) for r in latest)

    by_source: dict[str, dict] = {}
    for r in latest:
        key = f"{r['source_type']}:{r['source_id']}"
        bucket = by_source.setdefault(key, {
            "source_type": r["source_type"],
            "source_id": r["source_id"],
            "rx_rate_bps": 0,
            "tx_rate_bps": 0,
            "interface_count": 0,
        })
        bucket["rx_rate_bps"] += int(r.get("rx_rate_bps") or 0)
        bucket["tx_rate_bps"] += int(r.get("tx_rate_bps") or 0)
        bucket["interface_count"] += 1

    def _iface_label(name: str) -> str:
        return name[7:] if name.startswith("device/") else name

    return {
        "total_rx_bps": total_rx_bps,
        "total_tx_bps": total_tx_bps,
        "total_interfaces": len(latest),
        "top_talkers": [
            {
                "source_type": r["source_type"],
                "source_id": r["source_id"],
                "source_name": r.get("source_name") or r["source_id"],
                "interface_name": _iface_label(r["interface_name"]),
                "rx_rate_bps": int(r.get("rx_rate_bps") or 0),
                "tx_rate_bps": int(r.get("tx_rate_bps") or 0),
                "timestamp": (
                    r["timestamp"].isoformat() if isinstance(r["timestamp"], datetime) else r["timestamp"]
                ),
            }
            for r in sorted_samples[:20]
        ],
        "by_source": list(by_source.values()),
    }


async def get_bandwidth_interfaces(db: Any = None) -> list[dict]:
    """List all known interfaces with their latest rates (last hour)."""
    latest = await ch.get_latest_bandwidth_per_iface(since_hours=1)

    def _display(r: dict) -> str:
        name = r.get("source_name") or r["source_id"]
        if r["source_type"] == "agent":
            iface = r["interface_name"].replace("total", "all")
            return f"{name} ({iface})"
        return f"{name} / {r['interface_name']}"

    return [
        {
            "source_type": r["source_type"],
            "source_id": r["source_id"],
            "interface_name": r["interface_name"],
            "display_name": _display(r),
            "rx_rate_bps": int(r.get("rx_rate_bps") or 0),
            "tx_rate_bps": int(r.get("tx_rate_bps") or 0),
            "rx_bytes": int(r.get("rx_bytes") or 0),
            "tx_bytes": int(r.get("tx_bytes") or 0),
            "last_seen": (
                r["timestamp"].isoformat() if isinstance(r["timestamp"], datetime) else r["timestamp"]
            ),
        }
        for r in sorted(
            latest,
            key=lambda r: int(r.get("rx_rate_bps") or 0) + int(r.get("tx_rate_bps") or 0),
            reverse=True,
        )
    ]
