"""Fortinet FortiGate integration – system health, interfaces, IPsec, HA and licences.

Talks to the FortiOS REST API with an API token (System > Administrators >
REST API Admin). The token travels in an ``Authorization: Bearer`` header only,
never in the URL (``?access_token=`` would leak into proxy and device logs).

Endpoints (FortiOS 7.x):
  core      /api/v2/monitor/system/status
  optional  /api/v2/monitor/system/resource/usage
            /api/v2/monitor/system/interface    (link, counters)
            /api/v2/cmdb/system/interface       (admin status, type)
            /api/v2/monitor/vpn/ipsec
            /api/v2/cmdb/system/ha              (HA mode)
            /api/v2/monitor/system/ha-peer
            /api/v2/monitor/system/ha-checksums
            /api/v2/monitor/license/status
            /api/v2/monitor/system/firmware

Only the status request is core: when it fails the whole collect fails. Every
other part that cannot be read is named in ``data["unavailable"]`` and its
fields stay ``None`` / empty, which alerting and the UI treat as "unknown",
never as healthy.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from utils.net_safety import safe_async_client

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField, Threshold

# Interface types an unplugged cable is a real fault for. Tunnels, loopbacks and
# the like have no "link" in the cable sense.
_LINK_TYPES = frozenset({"physical", "aggregate", "redundant", "vlan"})
_HA_CLUSTER_MODES = frozenset({"a-p", "a-a"})


# ── Small helpers ─────────────────────────────────────────────────────────────


def _num(v) -> float | None:
    """A finite number or None (bools and strings are not numbers here)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return float(v)


def _int(v) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _results(body):
    return body.get("results") if isinstance(body, dict) else None


def _iso(epoch: float) -> str | None:
    """Epoch seconds (or milliseconds) as UTC ISO 8601."""
    if epoch > 1e11:
        epoch = epoch / 1000.0
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Parsers (pure) ────────────────────────────────────────────────────────────


def parse_status(body: dict) -> dict:
    """system/status. version, serial and build sit beside ``results``."""
    res = _results(body)
    res = res if isinstance(res, dict) else {}
    top = body if isinstance(body, dict) else {}
    model = " ".join(p for p in (_str(res.get("model_name")), _str(res.get("model_number"))) if p)
    return {
        "hostname": _str(res.get("hostname")),
        "model": model or _str(res.get("model")),
        "version": _str(top.get("version")) or _str(res.get("version")),
        "serial": _str(top.get("serial")) or _str(res.get("serial")),
        "build": _int(top.get("build")),
        "vdom": _str(top.get("vdom")) or _str(res.get("vdom")),
    }


def _current(v) -> float | None:
    """One resource: ``[{"current": 12, "historical": {...}}]`` (a list with one
    entry carrying per-interval history), sometimes a bare dict or number."""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("current")
    return _num(v)


def parse_resources(body: dict) -> dict:
    """resource/usage -> percentages and the session count (None when absent)."""
    res = _results(body)
    res = res if isinstance(res, dict) else {}
    cpu = _current(res.get("cpu"))
    mem = _current(res.get("mem"))
    return {
        "cpu_pct": cpu,
        "mem_pct": mem,
        "sessions": _int(_current(res.get("session"))),
        "session_rate": _int(_current(res.get("setuprate"))),
    }


def _iter_entries(results):
    """Interface-like results are a dict keyed by name or a list of dicts."""
    if isinstance(results, dict):
        for key, val in results.items():
            if isinstance(val, dict):
                yield key, val
    elif isinstance(results, list):
        for val in results:
            if isinstance(val, dict):
                yield _str(val.get("name")) or _str(val.get("id")), val


def parse_interfaces(monitor_body: dict, cmdb_body: dict | None) -> list[dict]:
    """Merge link/counters (monitor) with admin status/type (cmdb, may be None).

    ``admin_status`` stays None when the cmdb part could not be read; alerting
    then ignores the interface rather than guessing.
    """
    admin: dict[str, dict] = {}
    if cmdb_body is not None:
        for name, entry in _iter_entries(_results(cmdb_body)):
            admin[_str(entry.get("name")) or name] = entry
    out: list[dict] = []
    for key, entry in _iter_entries(_results(monitor_body)):
        name = _str(entry.get("name")) or _str(entry.get("id")) or key
        if not name:
            continue
        link = entry.get("link")
        counters = [_num(entry.get(k)) for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets")]
        known = [c for c in counters if c is not None]
        if not known:
            ever_active = None
        else:
            ever_active = any(c > 0 for c in known)
        cfg = admin.get(name, {})
        ip = _str(entry.get("ip"))
        mask = _int(entry.get("mask"))
        out.append({
            "name": name,
            "link": link if isinstance(link, bool) else None,
            "speed_mbps": _num(entry.get("speed")),
            "rx_bytes": _int(entry.get("rx_bytes")),
            "tx_bytes": _int(entry.get("tx_bytes")),
            "rx_errors": _int(entry.get("rx_errors")),
            "tx_errors": _int(entry.get("tx_errors")),
            "ipaddr": f"{ip}/{mask}" if ip and ip != "0.0.0.0" and mask is not None else (ip if ip != "0.0.0.0" else ""),
            "ever_active": ever_active,
            "admin_status": _str(cfg.get("status")).lower() or None,
            "type": _str(cfg.get("type")).lower() or None,
            "role": _str(cfg.get("role")) or None,
            "alias": _str(cfg.get("alias")) or None,
        })
    out.sort(key=lambda i: i["name"])
    return out


def parse_ipsec(body: dict) -> list[dict]:
    """vpn/ipsec -> tunnels with phase2 selectors.

    A tunnel is "up" when at least one phase2 is up, "down" when it has
    selectors and none is up, and "unknown" when the answer carries no status
    at all. Dial-up tunnels without a connected peer are not listed by FortiOS
    and therefore never appear here (and never alert).
    """
    res = _results(body)
    tunnels: list[dict] = []
    for t in res if isinstance(res, list) else []:
        if not isinstance(t, dict):
            continue
        name = _str(t.get("name")) or _str(t.get("tunnel_name"))
        if not name:
            continue
        p2s = []
        for p in t.get("proxyid") or []:
            if not isinstance(p, dict):
                continue
            p2s.append({
                "name": _str(p.get("p2name")) or _str(p.get("name")),
                "status": _str(p.get("status")).lower() or None,
                "incoming_bytes": _int(p.get("incoming_bytes")),
                "outgoing_bytes": _int(p.get("outgoing_bytes")),
            })
        statuses = [p["status"] for p in p2s if p["status"]]
        own = _str(t.get("status")).lower()
        if statuses:
            state = "up" if "up" in statuses else "down"
        elif own in ("up", "down"):
            state = own
        elif isinstance(t.get("proxyid"), list) and not p2s:
            state = "down"
        else:
            state = "unknown"
        tunnels.append({
            "name": name,
            "remote_gateway": _str(t.get("rgwy")),
            "type": _str(t.get("type")),
            "state": state,
            "phase2": p2s,
        })
    tunnels.sort(key=lambda t: t["name"])
    return tunnels


def _checksum_value(member: dict):
    cs = member.get("checksum")
    if isinstance(cs, dict):
        if "all" in cs:
            return cs["all"]
        return tuple(sorted((str(k), str(v)) for k, v in cs.items())) or None
    if isinstance(cs, (str, int)):
        return cs
    return None


def parse_ha(mode: str | None, peers_body: dict | None, checksums_body: dict | None,
             own_serial: str = "") -> dict:
    """Combine HA mode, peer list and config checksums into one state.

    state: standalone | healthy | peer_missing | out_of_sync | None (unknown).
    A cluster member that sees no peer is "peer_missing"; an unreadable part
    leaves the state None instead of guessing. ``ha-peer`` may list the local
    unit itself, so with ``own_serial`` known only other members count as peers.
    """
    peers: list[dict] | None = None
    if peers_body is not None:
        raw = _results(peers_body)
        peers = [{
            "hostname": _str(p.get("hostname")),
            "serial": _str(p.get("serial_no")) or _str(p.get("serial")),
            "priority": _int(p.get("priority")),
            "primary": p.get("master") if isinstance(p.get("master"), bool) else None,
        } for p in (raw if isinstance(raw, list) else []) if isinstance(p, dict)]

    in_sync: bool | None = None
    if checksums_body is not None:
        raw = _results(checksums_body)
        members = [m for m in (raw if isinstance(raw, list) else []) if isinstance(m, dict)]
        values = [_checksum_value(m) for m in members]
        if len(members) >= 2 and all(v is not None for v in values):
            in_sync = len(set(values)) == 1

    mode = (mode or "").lower() or None
    clustered = mode in _HA_CLUSTER_MODES or bool(peers)
    if mode == "standalone":
        state = "standalone"
    elif not clustered:
        state = None
    elif peers is None:
        state = None
    elif not [p for p in peers if not own_serial or p["serial"] != own_serial]:
        state = "peer_missing"
    elif in_sync is False:
        state = "out_of_sync"
    else:
        state = "healthy"
    return {
        "mode": mode,
        "state": state,
        "peers": peers,
        "peer_count": None if peers is None else len(peers),
        "in_sync": in_sync,
    }


def parse_licenses(body: dict) -> list[dict]:
    """license/status -> every sub-object that carries an ``expires`` epoch.

    Entries without a positive epoch (feature not purchased, perpetual) are
    omitted: they have no expiry to monitor, and "not licensed" is not a fault.
    """
    out: list[dict] = []

    def walk(node, path: list[str], depth: int) -> None:
        if depth > 6 or not isinstance(node, dict):
            return
        exp = _num(node.get("expires"))
        if exp is not None and exp > 0 and path:
            iso = _iso(exp)
            if iso:
                out.append({
                    "name": ".".join(path),
                    "status": _str(node.get("status")) or None,
                    "expires_at": iso,
                })
        for key, val in node.items():
            if isinstance(val, dict):
                walk(val, path + [str(key)], depth + 1)

    walk(_results(body), [], 0)
    out.sort(key=lambda lic: lic["name"])
    return out


def _version_tuple(entry: dict, version: str) -> tuple[int, ...] | None:
    """(major, minor, patch) from the entry's fields, else parsed from "v7.2.5"."""
    parts = [_int(entry.get(k)) for k in ("major", "minor", "patch")]
    if all(p is not None for p in parts):
        return tuple(parts)  # type: ignore[arg-type]
    nums = version.lstrip("vV").split(".")
    try:
        return tuple(int(n) for n in nums[:3]) if len(nums) >= 3 else None
    except ValueError:
        return None


def parse_firmware(body: dict) -> dict:
    """system/firmware -> running version and the newer versions on offer.

    FortiGuard also offers older images (downgrades); only versions newer than
    the running one count as an update. A version that cannot be compared is
    kept unless it equals the running one.
    """
    res = _results(body)
    res = res if isinstance(res, dict) else {}
    cur = res.get("current")
    current = _str(cur.get("version")) if isinstance(cur, dict) else ""
    cur_t = _version_tuple(cur, current) if isinstance(cur, dict) else None
    avail_raw = res.get("available")
    available = []
    for a in avail_raw if isinstance(avail_raw, list) else []:
        v = _str(a.get("version")) if isinstance(a, dict) else ""
        if not v or v in available or v == current:
            continue
        t = _version_tuple(a, v)
        if cur_t is not None and t is not None and t <= cur_t:
            continue
        available.append(v)
    return {"current": current or None, "available": available[:10], "update_available": bool(available)}


# ── API client ────────────────────────────────────────────────────────────────


class FortiGateAPI:
    def __init__(self, host: str, api_token: str, vdom: str = "", verify_ssl: bool = True):
        host = (host or "").strip()
        if host and not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        self.base = host.rstrip("/")
        self.api_token = api_token
        self.vdom = (vdom or "").strip()
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}

    def _params(self, extra: dict | None = None) -> dict:
        params = dict(extra or {})
        if self.vdom:
            params["vdom"] = self.vdom
        return params

    async def _get(self, client: httpx.AsyncClient, path: str, extra: dict | None = None) -> dict:
        resp = await client.get(f"{self.base}{path}", headers=self._headers(), params=self._params(extra))
        if resp.status_code in (401, 403):
            raise ValueError(f"FortiGate rejected the API token (HTTP {resp.status_code}); "
                             "check the token, its admin profile and the trusted hosts")
        if resp.status_code >= 400:
            raise ValueError(f"FortiGate answered HTTP {resp.status_code} for {path}")
        try:
            body = resp.json()
        except ValueError:
            raise ValueError(f"FortiGate answered a non-JSON body for {path}") from None
        if not isinstance(body, dict):
            raise ValueError(f"FortiGate answered an unexpected body for {path}")
        if body.get("status") == "error":
            raise ValueError(f"FortiGate reported an error for {path}")
        return body

    async def _optional(self, client: httpx.AsyncClient, label: str, path: str,
                        unavailable: set[str], extra: dict | None = None) -> dict | None:
        try:
            return await self._get(client, path, extra)
        except Exception:
            unavailable.add(label)
            return None

    async def fetch_all(self) -> dict:
        unavailable: set[str] = set()
        async with safe_async_client(verify=self.verify_ssl, timeout=20.0) as client:
            # Core request: a rejected token or an unreachable device must fail
            # the whole collect instead of producing a healthy-looking snapshot.
            status_body = await self._get(client, "/api/v2/monitor/system/status")
            if "results" not in status_body and "version" not in status_body:
                raise ValueError("Unexpected answer from system/status; is this a FortiGate?")

            usage, ifaces, ifaces_cfg, ipsec, ha_mode, lic, fw = await asyncio.gather(
                self._optional(client, "resource usage", "/api/v2/monitor/system/resource/usage", unavailable),
                self._optional(client, "interfaces", "/api/v2/monitor/system/interface", unavailable),
                self._optional(client, "interface admin status", "/api/v2/cmdb/system/interface",
                               unavailable, {"format": "name|status|type|role|alias"}),
                self._optional(client, "IPsec tunnels", "/api/v2/monitor/vpn/ipsec", unavailable),
                self._optional(client, "HA mode", "/api/v2/cmdb/system/ha", unavailable, {"format": "mode"}),
                self._optional(client, "licences", "/api/v2/monitor/license/status", unavailable),
                self._optional(client, "firmware updates", "/api/v2/monitor/system/firmware", unavailable),
            )

            mode = None
            if ha_mode is not None:
                res = _results(ha_mode)
                if isinstance(res, list) and res:
                    res = res[0]
                mode = _str(res.get("mode")) if isinstance(res, dict) else None
                if not mode:
                    unavailable.add("HA mode")
            peers = checksums = None
            if mode != "standalone":
                peers, checksums = await asyncio.gather(
                    self._optional(client, "HA peers", "/api/v2/monitor/system/ha-peer", unavailable),
                    self._optional(client, "HA checksums", "/api/v2/monitor/system/ha-checksums", unavailable),
                )

        system = parse_status(status_body)
        resources = parse_resources(usage) if usage is not None else {
            "cpu_pct": None, "mem_pct": None, "sessions": None, "session_rate": None}
        interfaces = parse_interfaces(ifaces, ifaces_cfg) if ifaces is not None else []
        return {
            "fw_type": "fortigate",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            **system,
            **resources,
            # System status carries no uptime on the monitor API.
            "uptime_s": None,
            "interfaces": interfaces if ifaces is not None else None,
            "ipsec": parse_ipsec(ipsec) if ipsec is not None else None,
            "ha": parse_ha(mode, peers, checksums, system["serial"]),
            "licenses": parse_licenses(lic) if lic is not None else None,
            "firmware": parse_firmware(fw) if fw is not None else None,
            "unavailable": sorted(unavailable),
        }

    async def health_check(self) -> bool:
        try:
            async with safe_async_client(verify=self.verify_ssl, timeout=10.0) as client:
                await self._get(client, "/api/v2/monitor/system/status")
            return True
        except Exception:
            return False


# ── Integration plugin ────────────────────────────────────────────────────────


class FortiGateIntegration(BaseIntegration):
    name = "fortigate"
    display_name = "FortiGate"
    icon = "fortinet"
    color = "red"
    description = "Monitor Fortinet FortiGate firewalls: CPU, memory, interfaces, IPsec, HA and licences."

    config_fields = [
        ConfigField(key="host", label="Host URL", field_type="url", placeholder="https://fortigate.local"),
        ConfigField(key="api_token", label="API Token", field_type="password", encrypted=True,
                    placeholder="REST API admin token (read-only profile is enough)"),
        ConfigField(key="vdom", label="VDOM", required=False, placeholder="root (leave empty for the default)"),
        ConfigField(key="verify_ssl", label="Verify SSL", field_type="checkbox", required=False, default=True),
    ]

    alert_thresholds = (
        Threshold("cpu_pct", "CPU", 90, "%", 1, 100),
        Threshold("mem_pct", "Memory", 90, "%", 1, 100),
        Threshold("license_warn_days", "Licence expiry warning", 30, "days", 1, 365),
        Threshold("license_crit_days", "Licence expiry critical", 7, "days", 1, 365),
    )

    def _api(self) -> FortiGateAPI:
        return FortiGateAPI(
            host=self.config["host"],
            api_token=self.config["api_token"],
            vdom=self.config.get("vdom", ""),
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            data = await self._api().fetch_all()
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc) or type(exc).__name__)

    async def health_check(self) -> bool:
        return await self._api().health_check()

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not isinstance(data, dict):
            return alerts

        # CPU / memory: a missing value is unknown and raises nothing.
        for key, title, entity in (("cpu_pct", "FortiGate CPU high", "cpu"),
                                   ("mem_pct", "FortiGate memory high", "memory")):
            val = _num(data.get(key))
            if val is not None and val >= self.threshold(key):
                alerts.append(Alert(severity="warning", title=title, detail=f"{val:.1f}%", entity=entity))

        # IPsec: a fully down tunnel is critical; one down phase2 beside a
        # working one is a warning for that selector.
        for t in data.get("ipsec") or []:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            name = t["name"]
            if t.get("state") == "down":
                alerts.append(Alert(severity="critical", title="IPsec tunnel down",
                                    detail=f"{name} ({t.get('remote_gateway') or 'no gateway'})",
                                    entity=f"ipsec: {name}"))
            elif t.get("state") == "up":
                for p in t.get("phase2") or []:
                    if isinstance(p, dict) and p.get("status") == "down" and p.get("name"):
                        alerts.append(Alert(severity="warning", title="IPsec phase2 down",
                                            detail=f"{name}/{p['name']}", entity=f"ipsec: {name}/{p['name']}"))

        # HA
        ha = data.get("ha")
        if isinstance(ha, dict):
            if ha.get("state") == "peer_missing":
                alerts.append(Alert(severity="critical", title="HA peer missing",
                                    detail=f"HA mode {ha.get('mode') or 'unknown'} but no peer is visible",
                                    entity="ha: peer"))
            elif ha.get("state") == "out_of_sync":
                alerts.append(Alert(severity="warning", title="HA cluster out of sync",
                                    detail="Configuration checksums differ between cluster members",
                                    entity="ha: sync"))

        # Interfaces. Conservative on purpose: alert only when the interface is
        # administratively up (so an intentionally disabled port is silent),
        # reports no link, is of a cabled type, AND has moved traffic since the
        # counters started (so a spare port that never had a cable stays quiet).
        # Counters reset on reboot, which errs on the side of not alerting.
        # Without the admin status (cmdb unreadable) no interface alerts at all.
        for i in data.get("interfaces") or []:
            if not isinstance(i, dict) or not i.get("name"):
                continue
            if (i.get("admin_status") == "up" and i.get("link") is False
                    and i.get("type") in _LINK_TYPES and i.get("ever_active") is True):
                alerts.append(Alert(severity="warning", title="Interface link down",
                                    detail=f"{i['name']} is enabled but has no link", entity=f"interface: {i['name']}"))

        # Licences, evaluated against the snapshot time so results are stable.
        now = _parse_iso(data.get("collected_at"))
        if now is not None:
            warn, crit = self.threshold("license_warn_days"), self.threshold("license_crit_days")
            for lic in data.get("licenses") or []:
                if not isinstance(lic, dict) or not lic.get("name"):
                    continue
                exp = _parse_iso(lic.get("expires_at"))
                if exp is None:
                    continue
                seconds = (exp - now).total_seconds()
                days = seconds / 86400
                entity = f"license: {lic['name']}"
                if seconds < 0:
                    alerts.append(Alert(severity="critical", title="Licence expired",
                                        detail=f"{lic['name']} expired on {exp.date().isoformat()}", entity=entity))
                elif days <= crit:
                    alerts.append(Alert(severity="critical", title="Licence expires soon",
                                        detail=f"{lic['name']} expires in {int(days)} day(s)", entity=entity))
                elif days <= warn:
                    alerts.append(Alert(severity="warning", title="Licence expires soon",
                                        detail=f"{lic['name']} expires in {int(days)} day(s)", entity=entity))
        return alerts
