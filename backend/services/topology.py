"""
Topology service — build device hierarchy and detect upstream failures.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost
from models.integration import IntegrationConfig
from services import snapshot as snap_svc

logger = logging.getLogger(__name__)


async def build_topology(db: AsyncSession) -> dict[int, int | None]:
    """Build topology tree: {host_id: parent_host_id or None}.

    Auto-detects parent-child from:
    - PingHost.parent_id (manual)
    - Proxmox cluster: VM/LXC → node
    - UniFi: Gateway → Switch → AP
    """
    # Load all hosts
    result = await db.execute(select(PingHost))
    all_hosts = result.scalars().all()

    topology: dict[int, int | None] = {}
    host_by_id = {h.id: h for h in all_hosts}

    # Name → id mapping (lowercase)
    name_map: dict[str, int] = {}
    ip_map: dict[str, int] = {}
    for h in all_hosts:
        topology[h.id] = getattr(h, "parent_id", None)
        name_map[h.name.lower()] = h.id
        raw = h.hostname
        for pfx in ("https://", "http://"):
            if raw.startswith(pfx):
                raw = raw[len(pfx):]
                break
        raw = raw.split("/")[0].split(":")[0]
        ip_map[raw] = h.id
        ip_map[h.name] = h.id

    # Auto-detect from Proxmox
    configs_result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.enabled == True,
            IntegrationConfig.type.in_(["proxmox", "unifi"]),
        )
    )
    configs = configs_result.scalars().all()

    for cfg in configs:
        snap = await snap_svc.get_latest(db, cfg.type, cfg.id)
        if not snap or not snap.ok or not snap.data_json:
            continue

        try:
            data = json.loads(snap.data_json)
        except (json.JSONDecodeError, TypeError):
            continue

        if cfg.type == "proxmox":
            for g in data.get("vms", []) + data.get("containers", []):
                guest_name = (g.get("name") or "").strip().lower()
                node_name = (g.get("node") or "").strip().lower()
                if not guest_name or not node_name:
                    continue
                guest_id = name_map.get(guest_name)
                node_id = name_map.get(node_name)
                if guest_id and node_id and guest_id != node_id:
                    if topology.get(guest_id) is None:
                        topology[guest_id] = node_id

        elif cfg.type == "unifi":
            devices = data.get("devices", [])
            clients = data.get("clients", [])
            gw_id = None
            sw_ids: list[int] = []
            ap_ids: list[int] = []
            # MAC → PingHost id for UniFi devices
            dev_mac_to_ph: dict[str, int] = {}
            for dev in devices:
                dev_ip = (dev.get("ip") or "").strip()
                dev_name = (dev.get("name") or "").strip()
                dev_mac = (dev.get("mac") or "").strip().lower()
                dtype = dev.get("type", "")
                ph_id = ip_map.get(dev_ip) or ip_map.get(dev_name) or name_map.get(dev_name.lower())
                if not ph_id:
                    continue
                if dev_mac:
                    dev_mac_to_ph[dev_mac] = ph_id
                if dtype in ("ugw", "usg", "udm", "udmpro", "uxg"):
                    gw_id = ph_id
                elif dtype == "usw":
                    sw_ids.append(ph_id)
                elif dtype == "uap":
                    ap_ids.append(ph_id)
            if gw_id:
                for sw_id in sw_ids:
                    if topology.get(sw_id) is None:
                        topology[sw_id] = gw_id
                parent_for_ap = sw_ids[0] if sw_ids else gw_id
                for ap_id in ap_ids:
                    if topology.get(ap_id) is None:
                        topology[ap_id] = parent_for_ap

            # Match non-UniFi hosts to switches via UniFi client table
            # UniFi clients have sw_mac (which switch they're on) and ip/mac
            mac_map: dict[str, int] = {}
            for h in all_hosts:
                if hasattr(h, "mac_address") and h.mac_address:
                    mac_map[h.mac_address.lower()] = h.id

            for cl in clients:
                cl_ip = (cl.get("ip") or "").strip()
                cl_mac = (cl.get("mac") or "").strip().lower()
                sw_mac = (cl.get("sw_mac") or "").strip().lower()
                if not sw_mac:
                    continue
                # Find the PingHost for this client
                cl_host_id = (
                    ip_map.get(cl_ip)
                    or mac_map.get(cl_mac)
                    or name_map.get((cl.get("hostname") or "").strip().lower())
                )
                if not cl_host_id:
                    continue
                # Find the switch PingHost
                sw_host_id = dev_mac_to_ph.get(sw_mac)
                if not sw_host_id or sw_host_id == cl_host_id:
                    continue
                # Only set if no parent yet (don't override Proxmox node→VM)
                if topology.get(cl_host_id) is None:
                    topology[cl_host_id] = sw_host_id

    return topology


def get_ancestors(topology: dict[int, int | None], host_id: int) -> list[int]:
    """Get list of ancestor host IDs (parent, grandparent, etc.)."""
    ancestors = []
    seen = set()
    current = host_id
    while current in topology:
        parent = topology[current]
        if parent is None or parent in seen:
            break
        ancestors.append(parent)
        seen.add(parent)
        current = parent
    return ancestors


def get_descendants(topology: dict[int, int | None], host_id: int) -> list[int]:
    """Get list of all descendant host IDs."""
    descendants = []
    queue = [host_id]
    seen = {host_id}
    while queue:
        current = queue.pop(0)
        for hid, parent in topology.items():
            if parent == current and hid not in seen:
                descendants.append(hid)
                seen.add(hid)
                queue.append(hid)
    return descendants


def filter_upstream_failures(
    offline_host_ids: set[int],
    topology: dict[int, int | None],
) -> tuple[set[int], set[int]]:
    """Partition offline hosts into primary failures vs cascaded (upstream down).

    Returns (primary_failures, cascaded).
    """
    primary = set()
    cascaded = set()

    for hid in offline_host_ids:
        ancestors = get_ancestors(topology, hid)
        if any(a in offline_host_ids for a in ancestors):
            cascaded.add(hid)
        else:
            primary.add(hid)

    return primary, cascaded
