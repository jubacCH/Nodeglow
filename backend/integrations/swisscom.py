"""Swisscom Internet-Box integration – router stats, WAN health, connected devices."""
from __future__ import annotations

import logging

from integrations._base import BaseIntegration, CollectorResult, ConfigField

logger = logging.getLogger(__name__)


# ── Sagemcom Client Wrapper ──────────────────────────────────────────────────


async def _fetch_swisscom(
    host: str, username: str, password: str, encryption: str, ssl: bool,
) -> dict:
    """Connect to the Swisscom Internet-Box via sagemcom-api and collect data."""
    from aiohttp import ClientSession
    from sagemcom_api.client import SagemcomClient
    from sagemcom_api.enums import EncryptionMethod

    enc = EncryptionMethod.SHA512 if encryption == "SHA512" else EncryptionMethod.MD5

    async with ClientSession() as session:
        client = SagemcomClient(
            host=host,
            username=username,
            password=password,
            authentication_method=enc,
            session=session,
            ssl=ssl,
            verify_ssl=False,
        )
        try:
            await client.login()

            # ── Device info ──
            info = await client.get_device_info()
            device_info = {
                "model": info.model_name or info.product_class or "Internet-Box",
                "serial": info.serial_number or "",
                "firmware": info.software_version or info.external_firmware_version or "",
                "mac": info.mac_address or "",
                "uptime_s": info.up_time or 0,
                "manufacturer": info.manufacturer or "Swisscom",
                "reboot_count": info.reboot_count,
            }

            # ── Connected hosts ──
            hosts_raw = await client.get_hosts(only_active=False)
            hosts = []
            active_count = 0
            for h in hosts_raw:
                active = getattr(h, "active", False) or False
                if active:
                    active_count += 1
                hosts.append({
                    "name": h.name or h.host_name or "",
                    "ip": h.ip_address or "",
                    "mac": (h.phys_address or "").upper(),
                    "active": active,
                    "interface": getattr(h, "interface_type", "") or "",
                    "device_type": getattr(h, "detected_device_type", "") or "",
                })

            # ── WAN / IP info via XPath (best-effort) ──
            wan_info: dict = {}
            xpaths_to_try = {
                "wan_ip": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/IPv4Addresses/IPv4Address[@uid='1']/IPAddress",
                "wan_gateway": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/IPv4Addresses/IPv4Address[@uid='1']/SubnetMask",
                "dns_servers": "Device/DNS/Client/Servers",
            }
            for key, xpath in xpaths_to_try.items():
                try:
                    val = await client.get_value_by_xpath(xpath)
                    wan_info[key] = val
                except Exception:
                    pass

            # ── WAN traffic stats (best-effort) ──
            traffic: dict = {}
            traffic_xpaths = {
                "wan_rx_bytes": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/Stats/BytesReceived",
                "wan_tx_bytes": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/Stats/BytesSent",
                "wan_rx_packets": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/Stats/PacketsReceived",
                "wan_tx_packets": "Device/IP/Interfaces/Interface[Alias='IP_DATA']/Stats/PacketsSent",
            }
            for key, xpath in traffic_xpaths.items():
                try:
                    val = await client.get_value_by_xpath(xpath)
                    if isinstance(val, (int, float, str)):
                        traffic[key] = int(val) if str(val).isdigit() else val
                    else:
                        traffic[key] = val
                except Exception:
                    pass

            return {
                "device": device_info,
                "hosts": hosts,
                "hosts_active": active_count,
                "hosts_total": len(hosts),
                "wan": wan_info,
                "traffic": traffic,
            }
        finally:
            await client.logout()


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
        ConfigField(
            key="encryption", label="Encryption", field_type="select",
            options=[
                {"value": "SHA512", "label": "SHA512 (default)"},
                {"value": "MD5", "label": "MD5 (older boxes)"},
            ],
            default="SHA512", required=False,
        ),
        ConfigField(
            key="ssl", label="Use HTTPS", field_type="checkbox",
            required=False, default=False,
        ),
    ]

    async def collect(self) -> CollectorResult:
        try:
            data = await _fetch_swisscom(
                host=self.config.get("host", "192.168.1.1"),
                username=self.config.get("username", "admin") or "admin",
                password=self.config.get("password", ""),
                encryption=self.config.get("encryption", "SHA512") or "SHA512",
                ssl=self.config.get("ssl", False),
            )
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        from aiohttp import ClientSession
        from sagemcom_api.client import SagemcomClient
        from sagemcom_api.enums import EncryptionMethod

        enc_str = self.config.get("encryption", "SHA512") or "SHA512"
        enc = EncryptionMethod.SHA512 if enc_str == "SHA512" else EncryptionMethod.MD5

        try:
            async with ClientSession() as session:
                client = SagemcomClient(
                    host=self.config.get("host", "192.168.1.1"),
                    username=self.config.get("username", "admin") or "admin",
                    password=self.config.get("password", ""),
                    authentication_method=enc,
                    session=session,
                    ssl=self.config.get("ssl", False),
                    verify_ssl=False,
                )
                await client.login()
                await client.logout()
                return True
        except Exception:
            return False
