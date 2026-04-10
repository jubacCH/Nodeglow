"""
Port discovery service – scans hosts for open ports and SSL certificates.

Runs periodically via the scheduler to discover open ports on monitored hosts
and extract SSL certificate info from TLS-enabled ports.
"""
import asyncio
import logging
import ssl as _ssl
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.discovered_port import DiscoveredPort
from models.ping import PingHost

logger = logging.getLogger(__name__)

# Common ports to scan (service_name, port)
COMMON_PORTS = [
    (21, "ftp"), (22, "ssh"), (23, "telnet"), (25, "smtp"),
    (53, "dns"), (80, "http"), (110, "pop3"), (143, "imap"),
    (443, "https"), (465, "smtps"), (587, "submission"),
    (993, "imaps"), (995, "pop3s"),
    (3306, "mysql"), (5432, "postgresql"), (6379, "redis"),
    (8080, "http-alt"), (8443, "https-alt"), (8006, "proxmox"),
    (8096, "jellyfin"), (8123, "hass"), (9090, "prometheus"),
    (3000, "grafana"), (5000, "registry"), (9443, "portainer"),
]

# Ports likely to have SSL/TLS
SSL_PORTS = {443, 465, 636, 993, 995, 8443, 8006, 9443}

SCAN_SEMAPHORE = asyncio.Semaphore(20)


async def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
    """Quick TCP connect check."""
    try:
        async with SCAN_SEMAPHORE:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
    except Exception:
        return False


async def _get_ssl_brief(hostname: str, port: int) -> dict | None:
    """Extract brief SSL cert info (issuer CN, subject CN, expiry days)."""
    try:
        loop = asyncio.get_event_loop()
        cert_pem = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: _ssl.get_server_certificate((hostname, port), timeout=4)
            ),
            timeout=6,
        )
        proc = await asyncio.create_subprocess_exec(
            "openssl", "x509", "-noout", "-enddate", "-issuer", "-subject",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate(input=cert_pem.encode())
        lines = stdout.decode().strip().split("\n")
        info: dict = {}
        for line in lines:
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().lower()
            if key == "notafter":
                info["expiry_raw"] = val.strip()
            elif key == "issuer":
                info["issuer"] = val.strip()
            elif key == "subject":
                info["subject"] = val.strip()

        # Parse CN from issuer/subject
        for field in ("issuer", "subject"):
            raw = info.get(field, "")
            for part in raw.split(","):
                part = part.strip()
                if part.upper().startswith("CN"):
                    _, _, cn = part.partition("=")
                    info[f"{field}_cn"] = cn.strip()
                    break

        # Expiry days
        if "expiry_raw" in info:
            expiry_dt = datetime.strptime(
                info["expiry_raw"], "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=timezone.utc)
            info["expiry_days"] = max(0, (expiry_dt - datetime.now(timezone.utc)).days)
            info["expiry_date"] = expiry_dt.strftime("%Y-%m-%d")

        return info
    except Exception:
        return None


async def scan_host_ports(hostname: str) -> list[dict]:
    """Scan a host for open ports from COMMON_PORTS. Returns list of open port dicts."""
    tasks = []
    for port, service in COMMON_PORTS:
        tasks.append((port, service, _tcp_check(hostname, port)))

    results = await asyncio.gather(*[t[2] for t in tasks])
    open_ports = []
    for (port, service, _), is_open in zip(tasks, results):
        if is_open:
            entry = {"port": port, "service": service, "has_ssl": False}
            # Check SSL on likely TLS ports or any 443-like port
            if port in SSL_PORTS or port == 443:
                ssl_info = await _get_ssl_brief(hostname, port)
                if ssl_info:
                    entry["has_ssl"] = True
                    entry["ssl_issuer"] = ssl_info.get("issuer_cn", ssl_info.get("issuer", ""))
                    entry["ssl_subject"] = ssl_info.get("subject_cn", ssl_info.get("subject", ""))
                    entry["ssl_expiry_days"] = ssl_info.get("expiry_days")
                    entry["ssl_expiry_date"] = ssl_info.get("expiry_date")
            open_ports.append(entry)
    return open_ports


async def discover_ports_for_host(db: AsyncSession, host: PingHost):
    """Scan a single host and upsert discovered ports."""
    # Strip protocol from hostname for raw TCP scan
    hostname = host.hostname
    for prefix in ("https://", "http://"):
        if hostname.startswith(prefix):
            hostname = hostname[len(prefix):]
    hostname = hostname.split("/")[0].split(":")[0]

    open_ports = await scan_host_ports(hostname)
    now = datetime.utcnow()

    # Get existing discovered ports for this host
    existing = (await db.execute(
        select(DiscoveredPort).where(DiscoveredPort.host_id == host.id)
    )).scalars().all()
    existing_map = {dp.port: dp for dp in existing}

    for op in open_ports:
        dp = existing_map.pop(op["port"], None)
        if dp:
            # Update existing
            dp.last_seen = now
            dp.last_open = True
            dp.service = op["service"]
            if op.get("has_ssl"):
                dp.has_ssl = True
                dp.ssl_issuer = op.get("ssl_issuer")
                dp.ssl_subject = op.get("ssl_subject")
                dp.ssl_expiry_days = op.get("ssl_expiry_days")
                dp.ssl_expiry_date = op.get("ssl_expiry_date")
        else:
            # New port found
            db.add(DiscoveredPort(
                host_id=host.id,
                port=op["port"],
                protocol="tcp",
                service=op["service"],
                has_ssl=op.get("has_ssl", False),
                ssl_issuer=op.get("ssl_issuer"),
                ssl_subject=op.get("ssl_subject"),
                ssl_expiry_days=op.get("ssl_expiry_days"),
                ssl_expiry_date=op.get("ssl_expiry_date"),
                first_seen=now,
                last_seen=now,
                last_open=True,
            ))

    # Mark ports that were NOT found as closed
    for dp in existing_map.values():
        dp.last_open = False

    await db.commit()


async def run_port_discovery():
    """Scheduled job: scan all enabled hosts for open ports."""
    from models.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        hosts = (await db.execute(
            select(PingHost).where(PingHost.enabled == True, PingHost.maintenance == False)
        )).scalars().all()

        if not hosts:
            return

        # Scan hosts with concurrency limit (5 hosts at a time)
        sem = asyncio.Semaphore(5)

        async def _scan(host):
            async with sem:
                try:
                    await discover_ports_for_host(db, host)
                except Exception as exc:
                    logger.warning("Port discovery failed for %s: %s", host.name, exc)

        await asyncio.gather(*[_scan(h) for h in hosts])
        logger.info("Port discovery completed for %d hosts", len(hosts))
