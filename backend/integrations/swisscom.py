"""Swisscom Internet-Box integration – device info, WAN status, connected devices.

Uses the native /ws/ REST API (Arcadyan IB5 platform) with X-Sah authentication.
No external library required — just httpx.
"""
from __future__ import annotations

import json
import logging

import httpx

from integrations._base import BaseIntegration, CollectorResult, ConfigField

logger = logging.getLogger(__name__)


# ── Internet-Box REST API Client ─────────────────────────────────────────────


class InternetBoxAPI:
    """Async client for Swisscom Internet-Box /ws/ REST API."""

    def __init__(self, host: str, password: str):
        if not host.startswith("http"):
            host = f"https://{host}"
        self.base = host.rstrip("/")
        self.password = password
        self._context: str | None = None

    async def _login(self, client: httpx.AsyncClient) -> str:
        """Authenticate and return contextID."""
        resp = await client.post(
            f"{self.base}/ws",
            content=json.dumps({
                "service": "sah.Device.Information",
                "method": "createContext",
                "parameters": {
                    "applicationName": "nodeglow",
                    "username": "admin",
                    "password": self.password,
                },
            }),
            headers={
                "Content-Type": "application/x-sah-ws-1-call+json",
                "Authorization": "X-Sah-Login",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        ctx = data.get("data", {}).get("contextID", "")
        if not ctx:
            raise ConnectionError("Login failed — no contextID returned")
        return ctx

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict:
        """GET /ws/<path> with auth header."""
        resp = await client.get(
            f"{self.base}/ws/{path}",
            headers={"Authorization": f"X-Sah {self._context}"},
        )
        resp.raise_for_status()
        return resp.json()

    def _params_dict(self, node: dict) -> dict[str, object]:
        """Extract {name: value} from a ws/ node's parameters list."""
        return {p["name"]: p.get("value", "") for p in node.get("parameters", [])}

    async def fetch_all(self) -> dict:
        """Collect all data from the Internet-Box."""
        async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
            self._context = await self._login(client)

            # ── Device Info ──
            di = await self._get(client, "DeviceInfo")
            di_params = self._params_dict(di)

            device = {
                "model": di_params.get("ModelName", "Internet-Box"),
                "serial": di_params.get("SerialNumber", ""),
                "firmware": di_params.get("SoftwareVersion", ""),
                "mac": di_params.get("BaseMAC", ""),
                "uptime_s": di_params.get("UpTime", 0),
                "manufacturer": di_params.get("Manufacturer", "Arcadyan"),
                "external_ip": di_params.get("ExternalIPAddress", ""),
                "hardware": di_params.get("HardwareVersion", ""),
                "status": di_params.get("DeviceStatus", ""),
                "reboots": di_params.get("NumberOfReboots", 0),
                "first_use": di_params.get("FirstUseDate", ""),
            }

            # ── Memory ──
            try:
                mem = await self._get(client, "DeviceInfo/MemoryStatus")
                mem_p = self._params_dict(mem)
                device["mem_total_kb"] = mem_p.get("Total", 0)
                device["mem_free_kb"] = mem_p.get("Free", 0)
                total = int(mem_p.get("Total", 0) or 0)
                free = int(mem_p.get("Free", 0) or 0)
                if total > 0:
                    device["mem_pct"] = round((total - free) / total * 100, 1)
            except Exception:
                pass

            # ── WAN Info ──
            wan: dict = {}
            try:
                nm = await self._get(client, "NMC")
                nm_p = self._params_dict(nm)
                wan["interface"] = nm_p.get("ActiveWANInterface", "")
            except Exception:
                pass
            try:
                wn = await self._get(client, "NetMaster/WAN")
                wan["physical"] = self._params_dict(wn).get("PhysicalInterface", "")
                for inst in wn.get("instances", []):
                    ip = self._params_dict(inst)
                    wan_name = ip.get("Name", "")
                    if wan_name:
                        wan[f"link_{wan_name}"] = ip.get("PhysicalInterface", "")
            except Exception:
                pass

            # ── Connected Devices ──
            hosts: list[dict] = []
            active_count = 0
            try:
                dev = await self._get(client, "Devices/Device")
                for inst in dev.get("instances", []):
                    p = self._params_dict(inst)
                    # Skip internal/self entries
                    src = p.get("DiscoverySource", "")
                    if src.startswith("self"):
                        continue
                    tags = p.get("Tags", "")
                    if "voice" in tags and "physical" in tags and "lan" not in tags:
                        continue

                    active = bool(p.get("Active", False))
                    if active:
                        active_count += 1

                    # Get IP addresses from children
                    ip_addr = ""
                    for child in inst.get("children", []):
                        cname = child.get("objectInfo", {}).get("name", "")
                        if cname == "IPv4Address":
                            for ip_inst in child.get("instances", []):
                                ip_p = self._params_dict(ip_inst)
                                addr = ip_p.get("Address", "")
                                if addr:
                                    ip_addr = addr
                                    break

                    hosts.append({
                        "name": p.get("Name", ""),
                        "mac": p.get("Key", ""),
                        "active": active,
                        "device_type": p.get("DeviceType", ""),
                        "ip": ip_addr,
                        "first_seen": p.get("FirstSeen", ""),
                        "last_connection": p.get("LastConnection", ""),
                    })
            except Exception:
                logger.exception("Failed to get devices")

            # ── WiFi Status ──
            wifi: dict = {}
            try:
                wf = await self._get(client, "NMC/Wifi")
                wf_p = self._params_dict(wf)
                wifi["enabled"] = bool(wf_p.get("Status", False))
                wifi["scheduler"] = bool(wf_p.get("Scheduler", False))
            except Exception:
                pass

            return {
                "device": device,
                "wan": wan,
                "wifi": wifi,
                "hosts": hosts,
                "hosts_active": active_count,
                "hosts_total": len(hosts),
            }

    async def health_check(self) -> bool:
        """Quick login test."""
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                await self._login(client)
                return True
        except Exception:
            return False


# ── Integration Plugin ────────────────────────────────────────────────────────


class SwisscomIntegration(BaseIntegration):
    name = "swisscom"
    display_name = "Swisscom Internet-Box"
    icon = "swisscom"
    color = "sky"
    description = "Monitor Swisscom Internet-Box: device info, WAN status, connected devices."

    config_fields = [
        ConfigField(
            key="host", label="Box IP / Hostname",
            placeholder="192.168.1.1",
        ),
        ConfigField(
            key="password", label="Admin Password",
            field_type="password", encrypted=True,
        ),
    ]

    def _api(self) -> InternetBoxAPI:
        return InternetBoxAPI(
            host=self.config.get("host", "192.168.1.1"),
            password=self.config.get("password", ""),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        return await self._api().health_check()
