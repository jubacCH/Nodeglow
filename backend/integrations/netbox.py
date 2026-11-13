"""NetBox integration – inventory counts and bounded lists, optional host import.

NetBox is an inventory, not a monitor: it knows what should exist, not whether
it is up. So the integration reports counts and bounded lists of sites,
devices, virtual machines and prefixes, and raises an alert only for devices an
operator marked ``failed``.

Optionally (checkbox "Import hosts", off by default) active devices and virtual
machines that have a primary IP are added as ping hosts, following the phpIPAM
import: ``collect()`` fetches, ``on_snapshot`` applies the snapshot to the host
table without any network access. The import only ever adds hosts. A host that
already exists (same address or name) is left exactly as it is, and nothing is
ever deleted.

The token needs read access (``view``) to ``dcim.site``, ``dcim.device``,
``virtualization.virtualmachine`` and ``ipam.prefix``. Pagination uses
``limit``/``offset`` and a cap per resource; the API's ``next`` link is not
followed because behind a reverse proxy it often points at the wrong host.
"""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, backend_only

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PAGE_LIMIT = 200
MAX_ITEMS = {"sites": 500, "devices": 1000, "vms": 1000, "prefixes": 500}
MAX_FAILED_DEVICES = 200

# resource key -> (path, label, permission named when access is denied)
_RESOURCES: dict[str, tuple[str, str, str]] = {
    "sites": ("/api/dcim/sites/", "sites", "dcim.view_site"),
    "devices": ("/api/dcim/devices/", "devices", "dcim.view_device"),
    "vms": ("/api/virtualization/virtual-machines/", "virtual machines", "virtualization.view_virtualmachine"),
    "prefixes": ("/api/ipam/prefixes/", "prefixes", "ipam.view_prefix"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _name(value: Any) -> str:
    """Display name of a nested NetBox object ({"name": ..} or {"display": ..})."""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("display") or "")
    return ""


def _status(value: Any) -> str:
    """Status as a lowercase slug: {"value": "active"} (3.x/4.x) or "active"."""
    if isinstance(value, dict):
        return str(value.get("value") or value.get("label") or "").strip().lower()
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _ip(value: Any) -> str:
    """Plain address of a primary_ip object ({"address": "192.0.2.10/24"}), else ""."""
    address = _d(value).get("address")
    if not isinstance(address, str) or not address:
        return ""
    try:
        return str(ipaddress.ip_interface(address.strip()).ip)
    except ValueError:
        return ""


def _utilization(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


# ── Parsers ───────────────────────────────────────────────────────────────────


def parse_site(item: dict) -> dict:
    return {
        "id": _int(item.get("id")),
        "name": str(item.get("name") or ""),
        "status": _status(item.get("status")),
        "region": _name(item.get("region")),
        "tenant": _name(item.get("tenant")),
        "device_count": _int(item.get("device_count")),
    }


def parse_device(item: dict) -> dict:
    ip4, ip6 = _ip(item.get("primary_ip4")), _ip(item.get("primary_ip6"))
    return {
        "id": _int(item.get("id")),
        "name": str(item.get("name") or ""),
        "status": _status(item.get("status")),
        # NetBox 4 renamed device_role to role.
        "role": _name(item.get("role")) or _name(item.get("device_role")),
        "type": str(_d(item.get("device_type")).get("model") or ""),
        "site": _name(item.get("site")),
        "tenant": _name(item.get("tenant")),
        "ip4": ip4,
        "ip6": ip6,
        "ip": ip4 or ip6,
    }


def parse_vm(item: dict) -> dict:
    ip4, ip6 = _ip(item.get("primary_ip4")), _ip(item.get("primary_ip6"))
    return {
        "id": _int(item.get("id")),
        "name": str(item.get("name") or ""),
        "status": _status(item.get("status")),
        "role": _name(item.get("role")),
        "cluster": _name(item.get("cluster")),
        "site": _name(item.get("site")),
        "tenant": _name(item.get("tenant")),
        "ip4": ip4,
        "ip6": ip6,
        "ip": ip4 or ip6,
    }


def parse_prefix(item: dict) -> dict:
    prefix = item.get("prefix")
    return {
        "id": _int(item.get("id")),
        "prefix": str(prefix) if prefix else "",
        "status": _status(item.get("status")),
        "vrf": _name(item.get("vrf")),
        # NetBox 4.2 replaced site with a generic scope.
        "scope": _name(item.get("scope")) or _name(item.get("site")),
        "tenant": _name(item.get("tenant")),
        "description": str(item.get("description") or "")[:120],
        # Not part of every NetBox version's prefix serializer: None = unknown.
        "utilization": _utilization(item.get("utilization")),
    }


def _status_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        key = r.get("status") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def build_snapshot(
    *,
    status: dict,
    raw: dict[str, tuple[list[dict], int | None, bool] | None],
    failed_devices: list[dict] | None,
    now: datetime,
    unavailable: set[str],
    source_url: str = "",
) -> dict:
    """Pure: API objects (None = could not be listed) -> bounded snapshot.

    ``raw`` maps a resource to (objects, API-reported total, truncated).
    """
    parsers = {"sites": parse_site, "devices": parse_device, "vms": parse_vm, "prefixes": parse_prefix}
    rows: dict[str, list[dict] | None] = {}
    totals: dict[str, int | None] = {}
    truncated: dict[str, bool] = {}
    for key, parse in parsers.items():
        entry = raw.get(key)
        if entry is None:
            rows[key], totals[key], truncated[key] = None, None, False
            continue
        objs, count, cut = entry
        rows[key] = [parse(o) for o in objs]
        truncated[key] = bool(cut)
        # The API's count is exact even when the list is cut; without it a cut
        # list gives no total at all rather than a wrong one.
        totals[key] = count if count is not None else (None if cut else len(rows[key]))

    def counts(key: str) -> dict[str, int] | None:
        return None if rows[key] is None else _status_counts(rows[key])

    prefix_rows = rows["prefixes"]
    return {
        "collected_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "version": str(status.get("netbox-version") or ""),
        "python_version": str(status.get("python-version") or ""),
        "workers_running": _int(status.get("rq-workers-running")),
        "totals": totals,
        # Counted over the stored rows: partial where `truncated` is set.
        "device_status": counts("devices"),
        "vm_status": counts("vms"),
        "sites": rows["sites"],
        "devices": rows["devices"],
        "vms": rows["vms"],
        "prefixes": prefix_rows,
        "failed_devices": failed_devices,
        "truncated": truncated,
        "unavailable": sorted(unavailable),
    }


# ── API Client ────────────────────────────────────────────────────────────────


class NetBoxError(Exception):
    """A request failed in a way worth reporting; the message is safe to show.
    ``short`` is the brief form for the snapshot's ``unavailable`` list."""

    def __init__(self, message: str, short: str = ""):
        super().__init__(message)
        self.short = short or message


class _Denied(NetBoxError):
    """HTTP 401/403 on one resource."""


class NetBoxAPI:
    def __init__(self, host: str, api_token: str, verify_ssl: bool = True):
        self.base = (host or "").strip().rstrip("/")
        self.token = (api_token or "").strip()
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        # NetBox 4.5 v2 tokens ("nbt_<key>.<secret>") use Bearer, v1 tokens Token.
        scheme = "Bearer" if self.token.startswith("nbt_") else "Token"
        return {"Authorization": f"{scheme} {self.token}", "Accept": "application/json"}

    def validate(self) -> None:
        if not self.base:
            raise NetBoxError("NetBox URL is not configured")
        if not self.token:
            raise NetBoxError("API token is not configured")

    async def _get(self, client, path: str, params: dict | None = None) -> dict:
        try:
            resp = await client.get(f"{self.base}{path}", headers=self._headers(), params=params)
        except Exception as exc:
            # No exception text: it could carry the request URL.
            raise NetBoxError(f"{type(exc).__name__} while requesting {path}",
                              short=type(exc).__name__) from None
        status = resp.status_code
        if status in (401, 403):
            # NetBox answers an invalid token with 403, a missing permission too.
            raise _Denied(f"HTTP {status} on {path}: check the API token and its permissions")
        if status >= 400:
            raise NetBoxError(f"HTTP {status} on {path}", short=f"HTTP {status}")
        try:
            body = resp.json()
        except ValueError:
            raise NetBoxError(f"Invalid JSON from {path}", short="invalid JSON") from None
        if not isinstance(body, dict):
            raise NetBoxError(f"Unexpected response from {path}", short="unexpected response")
        return body

    async def list_all(self, client, path: str, cap: int,
                       params: dict | None = None) -> tuple[list[dict], int | None, bool]:
        """Objects (at most ``cap``), the API's total count and whether the list was cut."""
        items: list[dict] = []
        count: int | None = None
        offset = 0
        while True:
            page = await self._get(client, path, {**(params or {}), "limit": PAGE_LIMIT, "offset": offset})
            results = _rows(page.get("results"))
            if count is None:
                count = _int(page.get("count"))
            items.extend(results)
            more = bool(page.get("next")) and bool(results)
            if len(items) >= cap:
                return items[:cap], count, more or len(items) > cap
            if not more:
                return items, count, False
            offset += len(results)

    async def fetch_all(self) -> dict:
        self.validate()
        async with safe_async_client(verify=self.verify_ssl, timeout=30.0, follow_redirects=False) as client:
            # The core request: unreachable or unauthenticated means no data at all.
            status = await self._get(client, "/api/status/")
            now = datetime.now(timezone.utc)

            raw: dict[str, tuple[list[dict], int | None, bool] | None] = {}
            unavailable: set[str] = set()
            errors: list[str] = []
            for key, (path, label, permission) in _RESOURCES.items():
                try:
                    raw[key] = await self.list_all(client, path, MAX_ITEMS[key])
                except _Denied:
                    raw[key] = None
                    unavailable.add(f"{label} (token lacks permission: {permission})")
                    errors.append(f"{label}: denied")
                except NetBoxError as exc:
                    raw[key] = None
                    unavailable.add(f"{label} ({exc.short})")
                    errors.append(f"{label}: {exc}")

            if all(v is None for v in raw.values()):
                raise NetBoxError("No resource could be read: " + "; ".join(errors))

            failed = await self._failed_devices(client, raw.get("devices"), unavailable)

        return build_snapshot(status=status, raw=raw, failed_devices=failed, now=now,
                              unavailable=unavailable, source_url=self.base)

    async def _failed_devices(self, client, devices, unavailable: set[str]) -> list[dict] | None:
        """Devices in status ``failed``. Read from the full list when it is
        complete, else asked for directly, so a cut list cannot hide one."""
        if devices is None:
            return None
        objs, _count, cut = devices
        if not cut:
            return [d for d in (parse_device(o) for o in objs) if d["status"] == "failed"][:MAX_FAILED_DEVICES]
        try:
            found, _c, _more = await self.list_all(client, _RESOURCES["devices"][0], MAX_FAILED_DEVICES,
                                                   params={"status": "failed"})
        except NetBoxError as exc:
            unavailable.add(f"failed devices ({exc.short if not isinstance(exc, _Denied) else 'denied'})")
            return None
        return [parse_device(o) for o in found]

    async def health_check(self) -> bool:
        try:
            self.validate()
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0, follow_redirects=False) as client:
                await self._get(client, "/api/status/")
            return True
        except Exception:
            return False


# ── Host import ───────────────────────────────────────────────────────────────
#
# Split like phpIPAM's: fetching the inventory is collect() (so a probe can do
# it inside the customer network); applying it to the host table is
# backend-only and works from the snapshot alone.


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def importable_entries(data: dict) -> tuple[list[dict], int]:
    """Active devices and VMs with a primary IP, and how many entries were left out."""
    entries: list[dict] = []
    skipped = 0
    for key in ("devices", "vms"):
        for row in _rows(data.get(key)):
            if row.get("status") != "active" or not (row.get("ip") or "").strip() or not (row.get("name") or "").strip():
                skipped += 1
                continue
            entries.append(row)
    return entries, skipped


@backend_only
async def apply_netbox_hosts(db: "AsyncSession", data: dict, source_detail: str) -> dict:
    """Add the importable devices and VMs of a NetBox snapshot as ping hosts.

    Add-only: a host whose address or name is already known is left untouched
    (no rename, no source change), and nothing is deleted.
    """
    from models.ping import PingHost
    from sqlalchemy import select

    entries, skipped = importable_entries(data)

    existing_q = await db.execute(select(PingHost))
    known: set[str] = set()
    for h in existing_q.scalars().all():
        # A host is also known by its display name and the first label of an
        # FQDN hostname ("sw-01.example.com" is NetBox's "sw-01"): pinging the
        # same device a second time by IP would be a duplicate.
        hostname = (h.hostname or "").strip().lower()
        short = hostname.split(".", 1)[0] if hostname and not _is_ip(hostname) else ""
        for value in (h.hostname, h.ip_address, h.name, short):
            if value:
                known.add(value.strip().lower())

    added = existing = 0
    errors: list[str] = []
    for row in entries:
        ip = row["ip"].strip()
        name = row["name"].strip()
        try:
            if ip.lower() in known or name.lower() in known:
                existing += 1
                continue
            db.add(PingHost(
                name=name[:128], hostname=ip, ip_address=ip, check_type="icmp",
                enabled=True, source="netbox", source_detail=source_detail,
            ))
            known.add(ip.lower())
            known.add(name.lower())
            added += 1
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if added:
        await db.commit()

    return {"added": added, "existing": existing, "skipped": skipped, "errors": errors}


# ── Integration Plugin ────────────────────────────────────────────────────────


class NetBoxIntegration(BaseIntegration):
    name = "netbox"
    display_name = "NetBox"
    icon = "netbox"
    color = "teal"
    description = "NetBox inventory: sites, devices, VMs and prefixes, with optional host import."
    # An inventory changes slowly; no need to poll it every minute.
    default_interval_seconds = 300

    config_fields = [
        ConfigField(key="host", label="NetBox URL", field_type="url",
                    placeholder="https://netbox.example.com"),
        ConfigField(key="api_token", label="API token", field_type="password", encrypted=True,
                    placeholder="Read-only token"),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox",
                    required=False, default=True),
        ConfigField(key="import_hosts", label="Import active devices and VMs with a primary IP as hosts",
                    field_type="checkbox", required=False, default=False),
    ]

    def _api(self) -> NetBoxAPI:
        return NetBoxAPI(
            host=self.config.get("host", ""),
            api_token=self.config.get("api_token", ""),
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except NetBoxError as exc:
            return CollectorResult(success=False, error=str(exc))
        except Exception as exc:
            logger.warning("NetBox collect failed: %s", type(exc).__name__)
            return CollectorResult(success=False, error=f"{type(exc).__name__}: unexpected error while collecting")

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts
        for dev in _rows(data.get("failed_devices")):
            # NetBox allows unnamed devices; the id still identifies one.
            name = str(dev.get("name") or (f"#{dev['id']}" if dev.get("id") is not None else ""))
            if not name or dev.get("status") != "failed":
                continue
            site = str(dev.get("site") or "")
            alerts.append(Alert(
                severity="warning",
                title="Device marked failed in NetBox",
                detail=f"{name}" + (f" ({site})" if site else "") + " has status failed",
                entity=f"device: {site}/{name}" if site else f"device: {name}",
                host=str(dev.get("ip") or ""),
            ))
        return alerts

    async def on_snapshot(self, data: dict, config: dict, db) -> None:
        """Optional host import; off unless the instance enabled it."""
        if (config or {}).get("import_hosts") is not True:
            return
        source = data.get("source_url") or (config or {}).get("host") or "NetBox"
        out = await apply_netbox_hosts(db, data, str(source))
        if out["errors"]:
            logger.warning("NetBox host import: %s", "; ".join(out["errors"][:5]))
