import asyncio
import ipaddress
import json
import socket
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from utils.ping import check_host
from database import PingHost, PingResult, get_db
from models.discovered_port import DiscoveredPort
from models.agent import Agent

router = APIRouter(prefix="/hosts")


async def _dns_resolve(hostname: str) -> dict:
    """Resolve hostname→IP or IP→hostname. Returns {ip, fqdn}, either may be None."""
    # Strip URL scheme / path to get raw host
    raw = hostname
    for prefix in ("https://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.split("/")[0].split(":")[0]

    loop = asyncio.get_event_loop()
    result: dict = {"ip": None, "fqdn": None}
    try:
        ipaddress.ip_address(raw)
        # It's an IP → reverse lookup
        result["ip"] = raw
        try:
            fqdn = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: socket.gethostbyaddr(raw)[0]),
                timeout=2.0,
            )
            result["fqdn"] = fqdn
        except Exception:
            pass
    except ValueError:
        # It's a hostname → forward lookup
        result["fqdn"] = raw
        try:
            ip = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: socket.gethostbyname(raw)),
                timeout=2.0,
            )
            result["ip"] = ip
        except Exception:
            pass
    return result


def _heatmap_30d(results_30d: list) -> list[dict]:
    """Return list of 30 dicts {date, color} for heatmap, oldest first."""
    now = datetime.utcnow().date()
    by_day: dict = {}
    for r in results_30d:
        d = r.timestamp.date()
        if d not in by_day:
            by_day[d] = {"total": 0, "ok": 0}
        by_day[d]["total"] += 1
        if r.success:
            by_day[d]["ok"] += 1

    result = []
    for i in range(29, -1, -1):
        day = now - timedelta(days=i)
        if day in by_day:
            pct = by_day[day]["ok"] / by_day[day]["total"] * 100
            color = "emerald" if pct >= 95 else "yellow" if pct >= 80 else "red"
        else:
            color = "slate"
        result.append({"date": day.strftime("%d.%m"), "color": color})
    return result


def _uptime_pct(results: list) -> float:
    if not results:
        return 0.0
    return round(sum(1 for r in results if r.success) / len(results) * 100, 1)


# ── API (JSON) ─────────────────────────────────────────────────────────────────

@router.get("/api/status")
async def api_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PingHost).where(PingHost.enabled == True))
    hosts = result.scalars().all()
    host_ids = [h.id for h in hosts]

    # Batch: latest result per host in one query (avoids N+1)
    latest_by_host: dict[int, PingResult] = {}
    if host_ids:
        latest_sub = (
            select(PingResult.host_id, func.max(PingResult.id).label("max_id"))
            .where(PingResult.host_id.in_(host_ids))
            .group_by(PingResult.host_id)
            .subquery()
        )
        latest_rows = (await db.execute(
            select(PingResult).join(latest_sub, PingResult.id == latest_sub.c.max_id)
        )).scalars().all()
        latest_by_host = {r.host_id: r for r in latest_rows}

    # Batch: last successful ping per host (for "last seen" on offline hosts)
    last_seen_by_host: dict[int, datetime] = {}
    if host_ids:
        last_ok_rows = (await db.execute(
            select(
                PingResult.host_id,
                func.max(PingResult.timestamp).label("last_ok"),
            )
            .where(PingResult.host_id.in_(host_ids), PingResult.success == True)
            .group_by(PingResult.host_id)
        )).all()
        last_seen_by_host = {r.host_id: r.last_ok for r in last_ok_rows if r.last_ok}

    # Batch: uptime stats (total/success counts per host for 24h, 7d, 30d)
    uptime_by_host: dict[int, dict] = {}
    if host_ids:
        now = datetime.utcnow()
        cutoff_30d = now - timedelta(days=30)
        rows = (await db.execute(
            select(
                PingResult.host_id,
                func.count().label("total"),
                func.sum(func.cast(PingResult.success, Integer)).label("ok"),
                func.min(PingResult.timestamp).label("oldest"),
            )
            .where(PingResult.host_id.in_(host_ids), PingResult.timestamp >= cutoff_30d)
            .group_by(PingResult.host_id)
        )).all()

        # Also get 24h and 7d counts
        cutoff_7d = now - timedelta(days=7)
        cutoff_24h = now - timedelta(hours=24)
        rows_7d = (await db.execute(
            select(
                PingResult.host_id,
                func.count().label("total"),
                func.sum(func.cast(PingResult.success, Integer)).label("ok"),
            )
            .where(PingResult.host_id.in_(host_ids), PingResult.timestamp >= cutoff_7d)
            .group_by(PingResult.host_id)
        )).all()
        rows_24h = (await db.execute(
            select(
                PingResult.host_id,
                func.count().label("total"),
                func.sum(func.cast(PingResult.success, Integer)).label("ok"),
            )
            .where(PingResult.host_id.in_(host_ids), PingResult.timestamp >= cutoff_24h)
            .group_by(PingResult.host_id)
        )).all()

        map_7d = {r.host_id: r for r in rows_7d}
        map_24h = {r.host_id: r for r in rows_24h}
        map_30d = {r.host_id: r for r in rows}

        for hid in host_ids:
            def _pct(row):
                if not row or not row.total:
                    return None
                return round((row.ok or 0) / row.total * 100, 1)
            uptime_by_host[hid] = {
                "h24": _pct(map_24h.get(hid)),
                "d7": _pct(map_7d.get(hid)),
                "d30": _pct(map_30d.get(hid)),
            }

    out = []
    for host in hosts:
        lr = latest_by_host.get(host.id)
        up = uptime_by_host.get(host.id, {})
        out.append({
            "id": host.id,
            "name": host.name,
            "hostname": host.hostname,
            "ip_address": getattr(host, "ip_address", None),
            "check_type": host.check_type or "icmp",
            "maintenance": host.maintenance or False,
            "enabled": host.enabled,
            "source": host.source or "manual",
            "source_detail": host.source_detail,
            "online": lr.success if lr else None,
            "latency_ms": lr.latency_ms if lr else None,
            "port_error": host.port_error or False,
            "check_detail": json.loads(host.check_detail) if host.check_detail else None,
            "uptime_h24": up.get("h24"),
            "uptime_d7": up.get("d7"),
            "uptime_d30": up.get("d30"),
            "last_seen": last_seen_by_host[host.id].isoformat() if host.id in last_seen_by_host else None,
        })
    return out


@router.get("/api/test/{host_id}")
async def test_ping(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if not host:
        return {"success": False, "error": "Host not found"}
    ok, port_error, latency, detail = await check_host(host)
    return {"success": ok, "port_error": port_error, "latency_ms": latency, "check_detail": detail}



# ── Manual ping ────────────────────────────────────────────────────────────────

@router.post("/{host_id}/check")
async def ping_check_now(host_id: int, db: AsyncSession = Depends(get_db)):
    """Run an immediate ping check for a single host and store the result."""
    host = await db.get(PingHost, host_id)
    if not host:
        return RedirectResponse(url="/hosts", status_code=303)

    # For agent-sourced hosts, check agent heartbeat instead of ICMP
    if host.source == "agent":
        from sqlalchemy import func as sa_func
        agent_r = await db.execute(
            select(Agent).where(sa_func.lower(Agent.hostname) == host.name.lower())
        )
        agent = agent_r.scalars().first()
        success = False
        if agent and agent.last_seen:
            success = (datetime.utcnow() - agent.last_seen).total_seconds() < 120
        latency = 0 if success else None
    else:
        import json as _json
        success, port_err, latency, detail = await check_host(host)
        host.port_error = port_err
        host.check_detail = _json.dumps(detail) if detail else None

    db.add(PingResult(
        host_id=host.id,
        timestamp=datetime.utcnow(),
        success=success,
        latency_ms=latency,
    ))
    await db.commit()

    return RedirectResponse(url=f"/hosts/{host_id}", status_code=303)


# ── CRUD actions ───────────────────────────────────────────────────────────────

@router.post("/add")
async def add_ping_host(
    name: str = Form(...),
    hostname: str = Form(...),
    check_types: List[str] = Form(default=["icmp"]),
    port: str = Form(""),
    latency_threshold_ms: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    from routers.integrations import _validate_host
    host_err = _validate_host(hostname.strip())
    if host_err:
        return RedirectResponse(url=f"/hosts?error={host_err}", status_code=303)
    check_type = ",".join(t.strip() for t in check_types if t.strip()) or "icmp"
    db.add(PingHost(
        name=name.strip(),
        hostname=hostname.strip(),
        check_type=check_type,
        port=int(port) if port.strip() else None,
        latency_threshold_ms=float(latency_threshold_ms) if latency_threshold_ms.strip() else None,
    ))
    await db.commit()
    return RedirectResponse(url="/hosts", status_code=303)


@router.post("/api/create")
async def api_create_host(request: Request, db: AsyncSession = Depends(get_db)):
    """JSON endpoint for creating a host from the SPA frontend."""
    body = await request.json()
    name = (body.get("name") or "").strip()
    hostname = (body.get("hostname") or "").strip()
    check_type = (body.get("check_type") or "icmp").strip()
    port_str = str(body.get("port") or "").strip()
    if not name or not hostname:
        return JSONResponse({"error": "name and hostname required"}, status_code=400)
    from routers.integrations import _validate_host
    host_err = _validate_host(hostname)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)
    host = PingHost(
        name=name,
        hostname=hostname,
        check_type=check_type,
        port=int(port_str) if port_str else None,
    )
    db.add(host)
    await db.commit()
    await db.refresh(host)
    return {"ok": True, "id": host.id}


@router.post("/{host_id}/edit")
async def edit_ping_host(
    host_id: int,
    name: str = Form(...),
    hostname: str = Form(...),
    check_types: List[str] = Form(default=["icmp"]),
    port: str = Form(""),
    latency_threshold_ms: str = Form(""),
    parent_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(PingHost, host_id)
    if host:
        host.name = name.strip()
        host.hostname = hostname.strip()
        host.check_type = ",".join(t.strip() for t in check_types if t.strip()) or "icmp"
        host.port = int(port) if port.strip() else None
        host.latency_threshold_ms = float(latency_threshold_ms) if latency_threshold_ms.strip() else None
        host.parent_id = int(parent_id) if parent_id.strip() else None
        await db.commit()
    return RedirectResponse(url=f"/hosts/{host_id}?tab=info&saved=1", status_code=303)


@router.post("/api/{host_id}/delete")
async def delete_ping_host(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if host:
        await db.delete(host)
        await db.commit()
    return RedirectResponse(url="/hosts", status_code=303)


@router.post("/{host_id}/toggle")
async def toggle_ping_host(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if host:
        host.enabled = not host.enabled
        await db.commit()
    return RedirectResponse(url=f"/hosts/{host_id}?tab=info", status_code=303)


@router.post("/{host_id}/maintenance")
async def toggle_maintenance(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(PingHost, host_id)
    if not host:
        return RedirectResponse(url="/hosts", status_code=303)

    form = await request.form()
    duration = form.get("duration", "")

    if host.maintenance and not duration:
        # Toggle off
        host.maintenance = False
        host.maintenance_until = None
    else:
        # Toggle on with optional duration
        host.maintenance = True
        if duration == "1h":
            host.maintenance_until = datetime.utcnow() + timedelta(hours=1)
        elif duration == "2h":
            host.maintenance_until = datetime.utcnow() + timedelta(hours=2)
        elif duration == "4h":
            host.maintenance_until = datetime.utcnow() + timedelta(hours=4)
        elif duration == "8h":
            host.maintenance_until = datetime.utcnow() + timedelta(hours=8)
        elif duration == "24h":
            host.maintenance_until = datetime.utcnow() + timedelta(hours=24)
        elif duration == "indefinite":
            host.maintenance_until = None
        else:
            # Default: indefinite (backwards compatible)
            host.maintenance_until = None
    await db.commit()
    return RedirectResponse(url=f"/hosts/{host_id}?tab=info", status_code=303)


@router.post("/api/{host_id}/maintenance")
async def toggle_maintenance_api(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """JSON API for toggling maintenance mode on a host."""
    host = await db.get(PingHost, host_id)
    if not host:
        return JSONResponse({"error": "Host not found"}, status_code=404)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    duration = body.get("duration", "")
    if host.maintenance and not duration:
        host.maintenance = False
        host.maintenance_until = None
    else:
        host.maintenance = True
        hours_map = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "24h": 24}
        if duration in hours_map:
            host.maintenance_until = datetime.utcnow() + timedelta(hours=hours_map[duration])
        else:
            host.maintenance_until = None
    await db.commit()
    return JSONResponse({"ok": True, "maintenance": host.maintenance})


@router.get("/api/search")
async def api_search_hosts(q: str = "", db: AsyncSession = Depends(get_db)):
    """Search hosts by name, hostname, or IP. Returns JSON for sidebar search."""
    query = (q or "").strip().lower()
    if not query or len(query) < 2:
        return []

    result = await db.execute(
        select(PingHost)
        .where(
            func.lower(PingHost.name).contains(query)
            | func.lower(PingHost.hostname).contains(query)
        )
        .order_by(PingHost.name)
        .limit(10)
    )
    hosts = result.scalars().all()
    if not hosts:
        return []

    # Get latest ping result for online status
    host_ids = [h.id for h in hosts]
    latest_sub = (
        select(PingResult.host_id, func.max(PingResult.id).label("max_id"))
        .where(PingResult.host_id.in_(host_ids))
        .group_by(PingResult.host_id)
        .subquery()
    )
    latest_rows = (await db.execute(
        select(PingResult).join(latest_sub, PingResult.id == latest_sub.c.max_id)
    )).scalars().all()
    latest_map = {r.host_id: r for r in latest_rows}

    return [
        {
            "id": h.id,
            "name": h.name or h.hostname,
            "hostname": h.hostname,
            "enabled": h.enabled,
            "online": latest_map[h.id].success if h.id in latest_map else None,
        }
        for h in hosts
    ]


# ── Port Discovery ────────────────────────────────────────────────────────────

@router.get("/api/{host_id}/discovered-ports")
async def get_discovered_ports(host_id: int, db: AsyncSession = Depends(get_db)):
    """Return discovered ports for a host."""
    ports = (await db.execute(
        select(DiscoveredPort)
        .where(DiscoveredPort.host_id == host_id)
        .order_by(DiscoveredPort.port)
    )).scalars().all()
    return [
        {
            "id": p.id, "port": p.port, "protocol": p.protocol,
            "service": p.service, "status": p.status,
            "has_ssl": p.has_ssl, "ssl_issuer": p.ssl_issuer,
            "ssl_subject": p.ssl_subject, "ssl_expiry_days": p.ssl_expiry_days,
            "ssl_expiry_date": p.ssl_expiry_date, "ssl_status": p.ssl_status,
            "first_seen": str(p.first_seen) if p.first_seen else None,
            "last_seen": str(p.last_seen) if p.last_seen else None,
            "last_open": p.last_open,
        }
        for p in ports
    ]


@router.patch("/api/{host_id}/discovered-ports/{port_id}")
async def update_discovered_port(host_id: int, port_id: int, request: Request,
                                  db: AsyncSession = Depends(get_db)):
    """Accept or dismiss a discovered port / SSL cert."""
    dp = (await db.execute(
        select(DiscoveredPort).where(
            DiscoveredPort.id == port_id,
            DiscoveredPort.host_id == host_id,
        )
    )).scalar_one_or_none()
    if not dp:
        return JSONResponse({"error": "Not found"}, status_code=404)

    body = await request.json()
    action = body.get("action")         # monitor_port | dismiss_port | monitor_ssl | dismiss_ssl

    host = (await db.execute(
        select(PingHost).where(PingHost.id == host_id)
    )).scalar_one_or_none()
    if not host:
        return JSONResponse({"error": "Host not found"}, status_code=404)

    if action == "monitor_port":
        # Add this port to host monitoring (tcp check type)
        existing_types = set(t.strip() for t in (host.check_type or "").split(",") if t.strip())
        existing_types.add("tcp")
        host.check_type = ",".join(sorted(existing_types))
        if host.port is None or host.port == 0:
            host.port = dp.port
        dp.status = "monitored"

    elif action == "unmonitor_port":
        # Remove tcp from check types
        existing_types = set(t.strip() for t in (host.check_type or "").split(",") if t.strip())
        existing_types.discard("tcp")
        host.check_type = ",".join(sorted(existing_types)) or "icmp"
        host.port_error = False
        host.check_detail = None
        dp.status = "new"

    elif action == "dismiss_port":
        dp.status = "dismissed"

    elif action == "monitor_ssl":
        # Add https check type and update SSL monitoring
        existing_types = set(t.strip() for t in (host.check_type or "").split(",") if t.strip())
        existing_types.add("https")
        host.check_type = ",".join(sorted(existing_types))
        if dp.ssl_expiry_days is not None:
            host.ssl_expiry_days = dp.ssl_expiry_days
        dp.ssl_status = "monitored"

    elif action == "unmonitor_ssl":
        # Remove https from check types
        existing_types = set(t.strip() for t in (host.check_type or "").split(",") if t.strip())
        existing_types.discard("https")
        host.check_type = ",".join(sorted(existing_types)) or "icmp"
        host.port_error = False
        host.check_detail = None
        dp.ssl_status = "new"

    elif action == "dismiss_ssl":
        dp.ssl_status = "dismissed"

    else:
        return JSONResponse({"error": "Invalid action"}, status_code=400)

    await db.commit()
    return {"ok": True, "status": dp.status, "ssl_status": dp.ssl_status}


@router.post("/api/{host_id}/scan-ports")
async def trigger_port_scan(host_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a port scan for a specific host."""
    host = (await db.execute(
        select(PingHost).where(PingHost.id == host_id)
    )).scalar_one_or_none()
    if not host:
        return JSONResponse({"error": "Host not found"}, status_code=404)

    from services.port_discovery import discover_ports_for_host
    await discover_ports_for_host(db, host)

    # Return updated ports
    ports = (await db.execute(
        select(DiscoveredPort)
        .where(DiscoveredPort.host_id == host_id)
        .order_by(DiscoveredPort.port)
    )).scalars().all()
    return {
        "ok": True,
        "ports": [
            {
                "id": p.id, "port": p.port, "protocol": p.protocol,
                "service": p.service, "status": p.status,
                "has_ssl": p.has_ssl, "ssl_issuer": p.ssl_issuer,
                "ssl_subject": p.ssl_subject, "ssl_expiry_days": p.ssl_expiry_days,
                "ssl_expiry_date": p.ssl_expiry_date, "ssl_status": p.ssl_status,
                "first_seen": str(p.first_seen) if p.first_seen else None,
                "last_seen": str(p.last_seen) if p.last_seen else None,
                "last_open": p.last_open,
            }
            for p in ports
        ],
    }
