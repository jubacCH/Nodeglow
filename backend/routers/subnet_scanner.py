"""Subnet Scanner – discover hosts in a CIDR range and cross-reference with monitored hosts."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from templating import templates

from models.base import get_db
from models.ping import PingHost
from utils.ping import ping_host

router = APIRouter()

# ── Helpers ──────────────────────────────────────────────────────────────────

MAX_SUBNET_SIZE = 1024  # /22 max to prevent accidental /8 scans
CONCURRENT_PINGS = 100


async def _reverse_dns(ip: str) -> str | None:
    """Reverse DNS lookup, returns hostname or None."""
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=2,
        )
        hostname = result[0]
        # Ignore if it just returns the IP back
        if hostname and hostname != ip:
            return hostname
    except (socket.herror, socket.gaierror, OSError, asyncio.TimeoutError):
        pass
    return None


async def _ping_one(sem: asyncio.Semaphore, ip: str) -> dict:
    async with sem:
        ok, latency = await ping_host(ip, timeout=1)
        dns_name = await _reverse_dns(ip) if ok else None
        return {
            "ip": ip,
            "alive": ok,
            "latency_ms": round(latency, 1) if latency else None,
            "dns_name": dns_name,
        }


async def scan_subnet(cidr: str) -> list[dict]:
    """Ping all hosts in a CIDR range concurrently."""
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]

    if len(hosts) > MAX_SUBNET_SIZE:
        raise ValueError(f"Subnet too large ({len(hosts)} hosts, max {MAX_SUBNET_SIZE})")

    sem = asyncio.Semaphore(CONCURRENT_PINGS)
    results = await asyncio.gather(*[_ping_one(sem, ip) for ip in hosts])
    return list(results)


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("/subnet-scanner")
async def subnet_scanner_page(request: Request):
    return templates.TemplateResponse(
        "subnet_scanner.html", {"request": request}
    )


@router.post("/api/subnet-scanner/scan")
async def api_scan_subnet(request: Request, db: AsyncSession = Depends(get_db)):
    """Scan a CIDR range and return results cross-referenced with monitored hosts."""
    body = await request.json()
    cidr = (body.get("cidr") or "").strip()

    # Validate CIDR
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return JSONResponse({"error": f"Invalid CIDR: {e}"}, status_code=400)

    host_count = sum(1 for _ in network.hosts())
    if host_count > MAX_SUBNET_SIZE:
        return JSONResponse(
            {"error": f"Subnet too large ({host_count} hosts, max {MAX_SUBNET_SIZE})"},
            status_code=400,
        )

    # Get all monitored hosts for cross-reference (by IP and by hostname)
    q = await db.execute(select(PingHost))
    all_hosts = q.scalars().all()
    monitored_by_key: dict[str, PingHost] = {}
    for h in all_hosts:
        hostname = (h.hostname or "").strip()
        # Strip protocol/port for matching
        for prefix in ("https://", "http://"):
            if hostname.startswith(prefix):
                hostname = hostname[len(prefix):]
        hostname = hostname.split("/")[0].split(":")[0]
        if hostname:
            monitored_by_key[hostname.lower()] = h
        # Also index by display name for hostname matching
        name = (h.name or "").strip()
        if name:
            monitored_by_key[name.lower()] = h

    # Scan
    scan_results = await scan_subnet(cidr)

    # Enrich with monitoring info – match by IP first, then by DNS name
    results = []
    for r in scan_results:
        host = monitored_by_key.get(r["ip"])
        if not host and r.get("dns_name"):
            # Try matching by full DNS name, then short hostname
            dns = r["dns_name"]
            host = monitored_by_key.get(dns.lower())
            if not host:
                short = dns.split(".")[0]
                host = monitored_by_key.get(short.lower())
        results.append({
            **r,
            "monitored": host is not None,
            "host_id": host.id if host else None,
            "host_name": host.name if host else None,
            "host_enabled": host.enabled if host else None,
            "source": host.source if host else None,
        })

    alive_count = sum(1 for r in results if r["alive"])
    monitored_count = sum(1 for r in results if r["monitored"])
    unmonitored_alive = sum(1 for r in results if r["alive"] and not r["monitored"])

    return JSONResponse({
        "cidr": str(network),
        "total": len(results),
        "alive": alive_count,
        "monitored": monitored_count,
        "unmonitored_alive": unmonitored_alive,
        "results": results,
        "scanned_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@router.post("/api/subnet-scanner/add-hosts")
async def api_add_hosts(request: Request, db: AsyncSession = Depends(get_db)):
    """Add discovered IPs as monitored hosts."""
    body = await request.json()
    hosts_to_add = body.get("hosts", [])  # [{ip, dns_name}, ...]

    # Backward compat: also accept flat "ips" list
    if not hosts_to_add:
        ips = body.get("ips", [])
        hosts_to_add = [{"ip": ip} for ip in ips] if ips else []

    if not hosts_to_add or not isinstance(hosts_to_add, list):
        return JSONResponse({"error": "No hosts provided"}, status_code=400)

    # Check which already exist
    q = await db.execute(select(PingHost))
    existing = {(h.hostname or "").strip() for h in q.scalars().all()}

    added = 0
    for entry in hosts_to_add:
        ip = (entry.get("ip") or "").strip() if isinstance(entry, dict) else str(entry).strip()
        dns_name = (entry.get("dns_name") or "").strip() if isinstance(entry, dict) else ""
        if not ip or ip in existing:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue

        # Use short hostname as display name if available
        display_name = dns_name.split(".")[0] if dns_name else ip

        host = PingHost(
            name=display_name,
            hostname=ip,
            check_type="icmp",
            enabled=True,
            source="scanner",
            source_detail=f"Subnet Scanner ({dns_name})" if dns_name else "Subnet Scanner",
        )
        db.add(host)
        existing.add(ip)
        added += 1

    if added:
        await db.commit()

    return JSONResponse({"added": added})
