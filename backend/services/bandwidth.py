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

from services import clickhouse_client as ch

from services.host_state import iso
logger = logging.getLogger(__name__)


# ── What "total traffic" adds up ─────────────────────────────────────────────

# Interface names this module writes for the rows a total is built from.
WAN = "wan"
AGENT_TOTAL = "total"

# In order of preference; the first one that has data is the total. Rates of
# interfaces are not additive in general: one download passes the gateway,
# several switch ports and the host itself, and the sum of all of them showed
# five times the real traffic. Keep in step with
# clickhouse_client.BANDWIDTH_TOTAL_BASES.
TOTAL_BASES = ("wan", "guests", "agents")


def total_basis_of(source_type: str, interface_name: str) -> str | None:
    """Which total an interface belongs to; None for one that is never summed
    (switch ports, device uplinks, an agent's single interfaces)."""
    if interface_name == WAN:
        return "wan"
    if source_type == "proxmox" and interface_name.startswith(("qemu/", "lxc/")):
        return "guests"
    if source_type == "agent" and interface_name == AGENT_TOTAL:
        return "agents"
    return None


# Interfaces that carry bytes a network card of the same host counts already:
# bridges and the container ends attached to them, bonds, tunnels, and the
# filter layers Windows lists next to each adapter. A Docker host reported
# forty times its real traffic with them in the sum.
_STACKED_PREFIXES = (
    "lo", "veth", "br-", "docker", "virbr", "vmbr", "cni", "flannel", "cali", "kube",
    "tap", "tun", "fwbr", "fwpr", "fwln", "wg", "tailscale", "zt", "bond", "team",
    "dummy", "sit", "gre", "ip6tnl", "vxlan", "podman", "lxcbr", "lxdbr", "incusbr",
    "loopback", "vethernet", "isatap", "teredo",
)
_STACKED_MARKERS = ("filter", "virtual", "pseudo", "qos packet scheduler", "npcap")


def carries_own_traffic(interface_name: str) -> bool:
    """True for an interface whose bytes are not counted again by another one
    of the same host. Decided by name: the agent sends nothing else."""
    name = (interface_name or "").strip().lower()
    if not name or name == AGENT_TOTAL:
        return False
    if "." in name or "@" in name:  # VLAN on top of a card
        return False
    if name.startswith(_STACKED_PREFIXES):
        return False
    return not any(marker in name for marker in _STACKED_MARKERS)


def agent_total(interfaces: list[dict]) -> tuple[int, int] | None:
    """A host's traffic: the byte counters of its own network cards added up.
    None when no interface qualifies."""
    own = [i for i in interfaces
           if carries_own_traffic(i.get("name") or i.get("interface") or "")
           and (i.get("rx_bytes") is not None or i.get("tx_bytes") is not None)]
    if not own:
        return None
    return (sum(int(i.get("rx_bytes") or 0) for i in own),
            sum(int(i.get("tx_bytes") or 0) for i in own))


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
    agent_id: int,
    body: dict,
    timestamp: datetime | None = None,
    *,
    tenant_id: int,
    source_name: str = "",
) -> int:
    """Extract bandwidth samples from an agent heartbeat payload.

    Returns the number of samples written to ClickHouse. ``tenant_id`` is the
    agent's tenant; every row carries it.
    """
    if not body:
        return 0
    # The agent sends ``network_interfaces`` and its own total at the top
    # level. Its Python predecessor nested both under ``network``; reading
    # only that, no agent's traffic was recorded after the switch.
    legacy = body.get("network") if isinstance(body.get("network"), dict) else {}
    interfaces = list(body.get("network_interfaces") or legacy.get("interfaces") or [])
    # The host's whole traffic, what the traffic total adds up (TOTAL_BASES).
    # Built here from the host's own network cards. The agent's own total
    # leaves out loopback only, so on a Docker host it counts each byte on
    # the card, on the bridge and on the container's end of it. It is used
    # where no interface can be told to be a card.
    total = agent_total(interfaces)
    if total is None and interfaces:
        total_rx = body.get("rx_bytes", legacy.get("rx_bytes"))
        total_tx = body.get("tx_bytes", legacy.get("tx_bytes"))
        if total_rx is not None or total_tx is not None:
            total = (int(total_rx or 0), int(total_tx or 0))
    if total is not None:
        interfaces.append({"name": AGENT_TOTAL, "rx_bytes": total[0], "tx_bytes": total[1]})
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
        for row in rows:
            row["tenant_id"] = tenant_id
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (agent bandwidth) failed: %s", exc)
    return len(rows)


# ── Proxmox bandwidth extraction ───────────────────────────────────────────


async def extract_proxmox_bandwidth(
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
    *,
    tenant_id: int,
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
        # The cluster resource list has no network counters for a node; the
        # snapshot fills in 0. A row of zeros every minute is not a reading.
        if not netin and not netout:
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
    # The snapshot lists guests as ``vms`` and ``containers``. This read
    # ``qemu`` and ``lxc``, which it never had: no guest was ever recorded.
    for vm_type, keys in (("qemu", ("vms", "qemu")), ("lxc", ("containers", "lxc"))):
        for vm in [g for key in keys for g in (data.get(key) or [])]:
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
        for row in rows:
            row["tenant_id"] = tenant_id
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (proxmox bandwidth) failed: %s", exc)
    return len(rows)


# ── UniFi bandwidth extraction ─────────────────────────────────────────────


async def extract_unifi_bandwidth(
    config_id: int,
    snapshot_data_json: str | dict | None,
    timestamp: datetime | None = None,
    *,
    tenant_id: int,
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

    # The Internet link, as the controller's WAN health reports it. This is
    # what the traffic total shows: every switch port on the way carries the
    # same bytes again.
    wan = data.get("wan") or {}
    if wan.get("status") and ("rx_bytes_r" in wan or "tx_bytes_r" in wan):
        rows.append({
            "timestamp": ts,
            "source_type": "unifi",
            "source_id": source_id,
            "source_name": source_name,
            "interface_name": WAN,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_rate_bps": int(wan.get("rx_bytes_r") or 0) * 8,
            "tx_rate_bps": int(wan.get("tx_bytes_r") or 0) * 8,
        })

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
        for row in rows:
            row["tenant_id"] = tenant_id
        try:
            await ch.insert_bandwidth_metrics(rows)
        except Exception as exc:
            logger.error("ClickHouse insert (unifi bandwidth) failed: %s", exc)
    return len(rows)


# ── Query helpers (read from ClickHouse) ─────────────────────────────────────


def _ts(value):
    """A sample's time as UTC with Z. ClickHouse hands back naive (or aware)
    UTC datetimes; without the Z a browser reads them as local time, and every
    interface looked an hour or two stale ("0 reporting")."""
    return iso(value) if isinstance(value, datetime) else value


async def get_bandwidth_history(
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
                _ts(r["timestamp"])
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


async def get_bandwidth_total_history(hours: int = 24) -> dict:
    """Download / upload over time for the traffic chart, on one basis.

    ``basis`` names what the points add up (see ``TOTAL_BASES``), ``None``
    when nothing that can be added up reported in the range.
    """
    rows = await ch.get_bandwidth_total_history_ch(hours=hours)
    basis = next((b for b in TOTAL_BASES if any(int(r.get(f"{b}_n") or 0) for r in rows)), None)
    if basis is None:
        return {"basis": None, "samples": []}
    return {"basis": basis, "samples": [
        {
            "timestamp": _ts(r["timestamp"]),
            "rx_rate_bps": int(r.get(f"{basis}_rx") or 0),
            "tx_rate_bps": int(r.get(f"{basis}_tx") or 0),
            "interfaces": int(r.get(f"{basis}_n") or 0),
        }
        for r in rows if int(r.get(f"{basis}_n") or 0)
    ]}


async def get_bandwidth_summary() -> dict:
    """Top talkers + total throughput, computed from latest sample per
    (source_type, source_id, interface_name) over the last hour."""
    latest = await ch.get_latest_bandwidth_per_iface(since_hours=1)

    sorted_samples = sorted(
        latest,
        key=lambda r: int(r.get("rx_rate_bps") or 0) + int(r.get("tx_rate_bps") or 0),
        reverse=True,
    )
    # One basis, never every interface: see TOTAL_BASES.
    kinds = [total_basis_of(r["source_type"], r["interface_name"]) for r in latest]
    basis = next((b for b in TOTAL_BASES if b in kinds), None)
    counted = [r for r, kind in zip(latest, kinds) if basis is not None and kind == basis]
    total_rx_bps = sum(int(r.get("rx_rate_bps") or 0) for r in counted)
    total_tx_bps = sum(int(r.get("tx_rate_bps") or 0) for r in counted)

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
        "total_basis": basis,
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
                    _ts(r["timestamp"])
                ),
            }
            for r in sorted_samples[:20]
        ],
        "by_source": list(by_source.values()),
    }


async def get_bandwidth_interfaces() -> list[dict]:
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
                _ts(r["timestamp"])
            ),
        }
        for r in sorted(
            latest,
            key=lambda r: int(r.get("rx_rate_bps") or 0) + int(r.get("tx_rate_bps") or 0),
            reverse=True,
        )
    ]
