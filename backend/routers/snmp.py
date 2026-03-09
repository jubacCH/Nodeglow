"""SNMP monitoring routes – MIB management, host config, polling."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from templating import templates

from models.base import get_db
from models.credential import Credential
from models.ping import PingHost
from models.snmp import SnmpHostConfig, SnmpMib, SnmpOid, SnmpResult
from services.snmp import (
    DEFAULT_OIDS, OID_PRESETS, import_mib, poll_host, seed_default_oids,
)

router = APIRouter()


# ── SNMP Management Page ────────────────────────────────────────────────────


@router.get("/snmp")
async def snmp_page(request: Request, db: AsyncSession = Depends(get_db)):
    # MIBs
    q = await db.execute(select(SnmpMib).order_by(SnmpMib.name))
    mibs = q.scalars().all()

    # OID count
    oid_count_q = await db.execute(select(func.count(SnmpOid.id)))
    oid_count = oid_count_q.scalar() or 0

    # Host configs with host info
    q = await db.execute(
        select(SnmpHostConfig, PingHost.name, PingHost.hostname)
        .join(PingHost, PingHost.id == SnmpHostConfig.host_id)
        .order_by(PingHost.name)
    )
    host_configs = [
        {"config": cfg, "host_name": name, "hostname": hostname}
        for cfg, name, hostname in q.all()
    ]

    # Credentials for dropdown
    q = await db.execute(
        select(Credential).where(Credential.type.in_(["snmp_v2c", "snmp_v3"]))
    )
    credentials = q.scalars().all()

    # Available hosts (not yet configured for SNMP)
    configured_ids = {hc["config"].host_id for hc in host_configs}
    q = await db.execute(
        select(PingHost).where(PingHost.enabled == True).order_by(PingHost.name)
    )
    available_hosts = [h for h in q.scalars().all() if h.id not in configured_ids]

    return templates.TemplateResponse("snmp.html", {
        "request": request,
        "mibs": mibs,
        "oid_count": oid_count,
        "host_configs": host_configs,
        "credentials": credentials,
        "available_hosts": available_hosts,
        "oid_presets": list(OID_PRESETS.keys()),
    })


# ── MIB Management ──────────────────────────────────────────────────────────


@router.post("/api/snmp/mibs/upload")
async def api_upload_mib(file: UploadFile = File(...),
                         db: AsyncSession = Depends(get_db)):
    """Upload and parse a MIB file."""
    content = await file.read()
    try:
        mib_text = content.decode("utf-8")
    except UnicodeDecodeError:
        mib_text = content.decode("latin-1")

    module_name, oid_count = await import_mib(db, file.filename or "unknown", mib_text)
    return JSONResponse({
        "module": module_name,
        "oids_added": oid_count,
    })


@router.delete("/api/snmp/mibs/{mib_id}")
async def api_delete_mib(mib_id: int, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(SnmpMib).where(SnmpMib.id == mib_id))
    mib = q.scalar_one_or_none()
    if mib:
        await db.execute(delete(SnmpOid).where(SnmpOid.mib_name == mib.name))
        await db.execute(delete(SnmpMib).where(SnmpMib.id == mib_id))
        await db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/snmp/mibs/seed-defaults")
async def api_seed_defaults(db: AsyncSession = Depends(get_db)):
    """Seed default OID definitions."""
    added = await seed_default_oids(db)
    return JSONResponse({"added": added})


@router.get("/api/snmp/oids")
async def api_list_oids(db: AsyncSession = Depends(get_db),
                        mib: str = "", search: str = ""):
    """List OIDs, optionally filtered by MIB or search term."""
    q = select(SnmpOid)
    if mib:
        q = q.where(SnmpOid.mib_name == mib)
    if search:
        q = q.where(
            SnmpOid.name.ilike(f"%{search}%") | SnmpOid.oid.like(f"%{search}%")
        )
    q = q.order_by(SnmpOid.oid).limit(200)
    result = await db.execute(q)
    oids = [
        {"oid": o.oid, "name": o.name, "mib": o.mib_name,
         "syntax": o.syntax, "description": o.description}
        for o in result.scalars().all()
    ]
    return JSONResponse({"oids": oids})


# ── SNMP Host Configuration ────────────────────────────────────────────────


@router.post("/api/snmp/hosts")
async def api_add_snmp_host(request: Request, db: AsyncSession = Depends(get_db)):
    """Configure SNMP monitoring for a host."""
    body = await request.json()
    host_id = int(body.get("host_id", 0))
    credential_id = body.get("credential_id")
    port = int(body.get("port", 161))
    poll_interval = int(body.get("poll_interval", 60))
    preset = body.get("preset", "system")
    custom_oids = body.get("oids", [])

    if not host_id:
        return JSONResponse({"error": "host_id required"}, status_code=400)

    # Check host exists
    hq = await db.execute(select(PingHost).where(PingHost.id == host_id))
    if not hq.scalar_one_or_none():
        return JSONResponse({"error": "Host not found"}, status_code=404)

    # Determine OIDs
    if custom_oids:
        oids = custom_oids
    elif preset and preset in OID_PRESETS:
        oids = OID_PRESETS[preset]
        if oids is None:
            # "full" preset = all defaults
            oids = list(DEFAULT_OIDS.keys())
    else:
        oids = None  # use defaults during polling

    config = SnmpHostConfig(
        host_id=host_id,
        credential_id=credential_id if credential_id else None,
        port=port,
        oids_json=json.dumps(oids) if oids else None,
        poll_interval=max(10, poll_interval),
        enabled=True,
    )
    db.add(config)
    await db.commit()

    return JSONResponse({"ok": True})


@router.delete("/api/snmp/hosts/{config_id}")
async def api_remove_snmp_host(config_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(SnmpHostConfig).where(SnmpHostConfig.id == config_id))
    await db.commit()
    return JSONResponse({"ok": True})


@router.patch("/api/snmp/hosts/{config_id}")
async def api_update_snmp_host(config_id: int, request: Request,
                               db: AsyncSession = Depends(get_db)):
    body = await request.json()
    q = await db.execute(select(SnmpHostConfig).where(SnmpHostConfig.id == config_id))
    cfg = q.scalar_one_or_none()
    if not cfg:
        return JSONResponse({"error": "Not found"}, status_code=404)

    if "enabled" in body:
        cfg.enabled = bool(body["enabled"])
    if "credential_id" in body:
        cfg.credential_id = body["credential_id"] or None
    if "port" in body:
        cfg.port = int(body["port"])
    if "poll_interval" in body:
        cfg.poll_interval = max(10, int(body["poll_interval"]))
    if "oids" in body:
        cfg.oids_json = json.dumps(body["oids"]) if body["oids"] else None
    if "thresholds_json" in body:
        cfg.thresholds_json = body["thresholds_json"] if body["thresholds_json"] else None

    await db.commit()
    return JSONResponse({"ok": True})


# ── SNMP Polling API ────────────────────────────────────────────────────────


@router.post("/api/snmp/hosts/{config_id}/poll")
async def api_poll_host(config_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger an SNMP poll for a host."""
    q = await db.execute(
        select(SnmpHostConfig, PingHost.hostname)
        .join(PingHost, PingHost.id == SnmpHostConfig.host_id)
        .where(SnmpHostConfig.id == config_id)
    )
    row = q.first()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)

    config, hostname = row
    # Strip protocol
    for prefix in ("https://", "http://"):
        if hostname.startswith(prefix):
            hostname = hostname[len(prefix):]
    hostname = hostname.split("/")[0].split(":")[0]

    result = await poll_host(db, config, hostname)
    await db.commit()

    return JSONResponse({"data": result, "ok": bool(result)})


@router.get("/api/snmp/hosts/{host_id}/results")
async def api_get_results(host_id: int, limit: int = 60,
                          db: AsyncSession = Depends(get_db)):
    """Get recent SNMP results for a host."""
    q = await db.execute(
        select(SnmpResult)
        .where(SnmpResult.host_id == host_id)
        .order_by(SnmpResult.timestamp.desc())
        .limit(limit)
    )
    results = []
    for r in q.scalars().all():
        data = json.loads(r.data_json) if r.data_json else {}
        results.append({
            "timestamp": r.timestamp.isoformat(),
            "data": data,
        })
    return JSONResponse({"results": list(reversed(results))})


# ── Scheduled SNMP Polling ──────────────────────────────────────────────────


async def run_snmp_polls():
    """Run all due SNMP polls (called by scheduler)."""
    from database import AsyncSessionLocal
    import logging

    logger = logging.getLogger(__name__)

    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(SnmpHostConfig, PingHost.hostname)
            .join(PingHost, PingHost.id == SnmpHostConfig.host_id)
            .where(SnmpHostConfig.enabled == True)
        )
        configs = q.all()

    for config, hostname in configs:
        # Check if poll is due
        if config.last_poll:
            from datetime import timedelta
            next_due = config.last_poll + timedelta(seconds=config.poll_interval)
            if datetime.utcnow() < next_due:
                continue

        # Strip protocol
        clean_host = hostname
        for prefix in ("https://", "http://"):
            if clean_host.startswith(prefix):
                clean_host = clean_host[len(prefix):]
        clean_host = clean_host.split("/")[0].split(":")[0]

        try:
            async with AsyncSessionLocal() as db:
                await poll_host(db, config, clean_host)
                await db.commit()
        except Exception as exc:
            logger.error("SNMP poll [%s] failed: %s", hostname, exc)
