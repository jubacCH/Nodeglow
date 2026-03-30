"""Proxmox VE integration – cluster health, node metrics, VM/LXC status."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField


# ── API Client ────────────────────────────────────────────────────────────────


class ProxmoxAPI:
    """Thin async client for the Proxmox REST API."""

    def __init__(self, host: str, token_id: str, token_secret: str, verify_ssl: bool = True):
        self.base = host.rstrip("/")
        self._headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._verify_ssl = verify_ssl

    async def get(self, path: str) -> list | dict:
        url = f"{self.base}/api2/json{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl, timeout=10.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def post(self, path: str, data: dict | None = None) -> dict:
        url = f"{self.base}/api2/json{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl, timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, data=data or {})
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def cluster_status(self) -> list[dict]:
        return await self.get("/cluster/status")

    async def cluster_resources(self) -> list[dict]:
        return await self.get("/cluster/resources")

    async def vm_config(self, node: str, vmid: int) -> dict:
        try:
            return await self.get(f"/nodes/{node}/qemu/{vmid}/config")
        except Exception:
            return {}

    async def lxc_config(self, node: str, ctid: int) -> dict:
        try:
            return await self.get(f"/nodes/{node}/lxc/{ctid}/config")
        except Exception:
            return {}

    async def fetch_guest_macs(self, guests: list[dict]) -> dict[int, str]:
        _mac_re = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", re.I)

        async def _one(g):
            vmid = g.get("id") or g.get("vmid")
            node = g.get("node", "")
            gtype = g.get("type", "VM")
            try:
                cfg = await (self.vm_config(node, vmid) if gtype == "VM"
                             else self.lxc_config(node, vmid))
                for key in sorted(cfg.keys()):
                    if not key.startswith("net"):
                        continue
                    val = str(cfg[key])
                    m = _mac_re.search(val)
                    if m:
                        return vmid, m.group(1).upper()
            except Exception:
                pass
            return vmid, None

        results = await asyncio.gather(*[_one(g) for g in guests])
        return {vmid: mac for vmid, mac in results if mac}

    async def cluster_tasks(self) -> list[dict]:
        """Fetch recent cluster-wide tasks."""
        try:
            return await self.get("/cluster/tasks")
        except Exception:
            return []

    async def health_check(self) -> bool:
        try:
            await self.cluster_status()
            return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def parse_cluster_data(resources: list[dict], cluster_status: list[dict],
                       tasks: list[dict] | None = None) -> dict:
    quorum_ok = True
    cluster_name = "Proxmox Cluster"
    for item in cluster_status:
        if item.get("type") == "cluster":
            quorum_ok = bool(item.get("quorate", 1))
            cluster_name = item.get("name", cluster_name)

    nodes = []
    vms = []
    containers = []

    for r in resources:
        rtype = r.get("type")

        if rtype == "node":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            mem_pct = round(mem_used / mem_total * 100, 1)
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            uptime_s = r.get("uptime") or 0
            nodes.append({
                "name": r.get("node", r.get("name", "?")),
                "status": r.get("status", "unknown"),
                "online": r.get("status") == "online",
                "cpu_pct": cpu_pct,
                "mem_pct": mem_pct,
                "mem_used_gb": round(mem_used / 1024**3, 1),
                "mem_total_gb": round(mem_total / 1024**3, 1),
                "disk_used_gb": round(disk_used / 1024**3, 1),
                "disk_total_gb": round(disk_total / 1024**3, 1),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "uptime_s": uptime_s,
            })

        elif rtype == "qemu":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            vms.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"vm-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "disk_used_gb": round(disk_used / 1024**3, 2),
                "disk_total_gb": round(disk_total / 1024**3, 2),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "diskread": r.get("diskread") or 0,
                "diskwrite": r.get("diskwrite") or 0,
                "uptime_s": r.get("uptime") or 0,
                "type": "VM",
            })

        elif rtype == "lxc":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            containers.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"ct-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "disk_used_gb": round(disk_used / 1024**3, 2),
                "disk_total_gb": round(disk_total / 1024**3, 2),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "diskread": r.get("diskread") or 0,
                "diskwrite": r.get("diskwrite") or 0,
                "uptime_s": r.get("uptime") or 0,
                "type": "LXC",
            })

    nodes.sort(key=lambda n: n["name"])
    vms.sort(key=lambda v: (v["node"], v["name"]))
    containers.sort(key=lambda c: (c["node"], c["name"]))

    online_nodes = [n for n in nodes if n["online"]]
    cpu_used = sum(n["cpu_pct"] for n in online_nodes)
    mem_used_gb = sum(n["mem_used_gb"] for n in online_nodes)
    mem_total_gb = sum(n["mem_total_gb"] for n in online_nodes)

    totals = {
        "nodes_total": len(nodes),
        "nodes_online": len(online_nodes),
        "vms_total": len(vms),
        "vms_running": sum(1 for v in vms if v["running"]),
        "lxc_total": len(containers),
        "lxc_running": sum(1 for c in containers if c["running"]),
        "cpu_avg_pct": round(cpu_used / len(online_nodes), 1) if online_nodes else 0,
        "mem_used_gb": round(mem_used_gb, 1),
        "mem_total_gb": round(mem_total_gb, 1),
        "mem_pct": round(mem_used_gb / mem_total_gb * 100, 1) if mem_total_gb else 0,
    }

    # Parse tasks
    parsed_tasks = []
    for t in (tasks or []):
        starttime = t.get("starttime") or 0
        endtime = t.get("endtime")
        status = t.get("status", "")
        # Store as naive UTC datetime strings — localtime filter handles tz
        start_dt = datetime.utcfromtimestamp(starttime) if starttime else None
        parsed_tasks.append({
            "starttime": start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else None,
            "_sort": starttime,
            "node": t.get("node", ""),
            "user": t.get("user", ""),
            "type": t.get("type", ""),
            "id": t.get("id", ""),
            "status": status if status else "running",
            "ok": status == "OK" if status else None,
        })
    parsed_tasks.sort(key=lambda x: x["_sort"], reverse=True)
    for t in parsed_tasks:
        del t["_sort"]

    return {
        "quorum_ok": quorum_ok,
        "cluster_name": cluster_name,
        "nodes": nodes,
        "vms": vms,
        "containers": containers,
        "totals": totals,
        "tasks": parsed_tasks,
    }


# ── Host Import ───────────────────────────────────────────────────────────────


async def import_proxmox_hosts(cluster_name: str, data: dict, db) -> dict:
    from models.ping import PingHost
    from sqlalchemy import select

    existing_q = await db.execute(select(PingHost))
    existing_all: list[PingHost] = existing_q.scalars().all()
    by_hostname: dict[str, PingHost] = {h.hostname: h for h in existing_all}
    # Index proxmox-sourced hosts by source_detail for stable VMID matching
    by_source_key: dict[str, PingHost] = {}
    for h in existing_all:
        if h.source == "proxmox" and h.source_detail and ":" in h.source_detail:
            by_source_key[h.source_detail] = h

    # Import nodes (physical hosts) + VMs + LXCs
    all_entries = []
    for node in data.get("nodes", []):
        all_entries.append({
            **node,
            "running": node.get("online", False),
            "_is_node": True,
        })
    for g in data.get("vms", []) + data.get("containers", []):
        all_entries.append(g)

    added = merged = skipped = 0
    dirty = False

    for g in all_entries:
        hostname = (g.get("name") or "").strip()
        if not hostname:
            skipped += 1
            continue

        # Build stable source key: "cluster:vmid" for VMs/LXCs, "cluster:node:name" for nodes
        vmid = g.get("id")
        if vmid and not g.get("_is_node"):
            source_key = f"{cluster_name}:{vmid}"
        else:
            source_key = f"{cluster_name}:node:{hostname}"

        # Match by stable source_key first, fall back to hostname
        host = by_source_key.get(source_key) or by_hostname.get(hostname)

        if host:
            changed = False
            if host.source == "manual":
                host.source = "proxmox"
                changed = True
            # Update name if it changed in Proxmox
            if host.source == "proxmox" and host.name != hostname:
                logger.info("Proxmox rename: %s → %s", host.name, hostname)
                host.name = hostname
                host.hostname = hostname
                changed = True
            # Store stable source_key for future matching
            if host.source_detail != source_key:
                host.source_detail = source_key
                changed = True
            if changed:
                dirty = True
            merged += 1
        else:
            new_host = PingHost(
                name=hostname,
                hostname=hostname,
                check_type="icmp",
                enabled=g.get("running", False),
                source="proxmox",
                source_detail=source_key,
            )
            db.add(new_host)
            by_hostname[hostname] = new_host
            by_source_key[source_key] = new_host
            added += 1
            dirty = True

    if dirty:
        await db.commit()

    return {"added": added, "merged": merged, "skipped": skipped}


# ── Integration Plugin ────────────────────────────────────────────────────────


class ProxmoxIntegration(BaseIntegration):
    name = "proxmox"
    display_name = "Proxmox VE"
    icon = "proxmox"
    color = "orange"
    description = "Monitor Proxmox VE clusters via the REST API."

    config_fields = [
        ConfigField(key="host", label="Host URL", field_type="url",
                    placeholder="https://proxmox.local:8006"),
        ConfigField(key="token_id", label="Token ID",
                    placeholder="user@realm!tokenid"),
        ConfigField(key="token_secret", label="Token Secret",
                    field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL",
                    field_type="checkbox", required=False, default=True),
        ConfigField(key="ssh_user", label="SSH User (for deploy)",
                    placeholder="root", required=False, default="root"),
        ConfigField(key="ssh_private_key", label="SSH Private Key",
                    field_type="password", encrypted=True, required=False,
                    placeholder="Paste private key for pct exec on Proxmox node"),
    ]

    def _api(self) -> ProxmoxAPI:
        return ProxmoxAPI(
            host=self.config["host"],
            token_id=self.config["token_id"],
            token_secret=self.config["token_secret"],
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            api = self._api()
            resources, status, tasks = await asyncio.gather(
                api.cluster_resources(),
                api.cluster_status(),
                api.cluster_tasks(),
            )
            data = parse_cluster_data(resources, status, tasks)
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not data.get("quorum_ok", True):
            alerts.append(Alert(severity="critical", title="Cluster quorum lost",
                                detail=f"Cluster: {data.get('cluster_name', '?')}"))
        for node in data.get("nodes", []):
            if not node.get("online", True):
                alerts.append(Alert(severity="critical", title="Node offline",
                                    detail=node.get("name", "?"), entity=f"node: {node.get('name', '?')}"))
            if node.get("cpu_pct", 0) >= 95:
                alerts.append(Alert(severity="warning", title="Node CPU critical",
                                    detail=f"{node.get('name', '?')}: {node.get('cpu_pct')}%",
                                    entity=f"node: {node.get('name', '?')}"))
            if node.get("mem_pct", 0) >= 95:
                alerts.append(Alert(severity="warning", title="Node memory critical",
                                    detail=f"{node.get('name', '?')}: {node.get('mem_pct')}%",
                                    entity=f"node: {node.get('name', '?')}"))
        return alerts

    async def health_check(self) -> bool:
        return await self._api().health_check()

    async def on_snapshot(self, data: dict, config: dict, db) -> None:
        """Auto-import VMs/LXCs as ping hosts after each successful collect."""
        cluster_name = data.get("cluster_name", config.get("host", "Proxmox"))
        await import_proxmox_hosts(cluster_name, data, db)

    def get_detail_context(self, data: dict, config: dict) -> dict:
        """Provide parsed data for the Proxmox detail template."""
        nodes = data.get("nodes", [])
        vms = data.get("vms", [])
        containers = data.get("containers", [])
        totals = data.get("totals", {})
        return {
            "nodes": nodes,
            "vms": vms,
            "containers": containers,
            "totals": totals,
            "quorum_ok": data.get("quorum_ok", True),
            "cluster_name": data.get("cluster_name", ""),
            "tasks": data.get("tasks", []),
        }
