"""/api/v1/sites — network locations inside a tenant (B-14, concept F phase 3, PR 4).

See ``services/sites.py`` for the effective site of a host. Reading is open to
every role; creating, renaming and deleting sites is for admins and audited.
Deleting a site moves its hosts, probes, install tokens and scan schedules to
the default site; the default site itself cannot be deleted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent import Agent
from models.agent_install_token import AgentInstallToken
from models.api_key import ApiKey
from models.base import get_db
from models.ping import PingHost
from models.scanner import SubnetScanSchedule
from models.tenant import Site
from routers.api_v1 import require_admin, require_api_key
from services import sites as site_svc
from services.audit import log_action
from services.host_state import iso

router = APIRouter(prefix="/api/v1/sites", tags=["Sites"])

# Tables whose rows carry a site; a deleted site's rows move to the default.
SITE_TABLES = (PingHost, Agent, AgentInstallToken, SubnetScanSchedule)


async def _name_taken(db, name: str, exclude_id: int | None = None) -> bool:
    q = select(Site.id).where(func.lower(Site.name) == name.lower())
    if exclude_id is not None:
        q = q.where(Site.id != exclude_id)
    return (await db.execute(q)).first() is not None


@router.get("", summary="Sites with their host and probe counts")
async def list_sites(db: AsyncSession = Depends(get_db), _key: ApiKey = Depends(require_api_key)):
    ctx = await site_svc.load_context(db)
    hosts = (await db.execute(select(PingHost.site_id, PingHost.probe_id))).all()
    host_count: dict[int | None, int] = {}
    for site_id, probe_id in hosts:
        eff = ctx.effective(site_id, probe_id)
        host_count[eff] = host_count.get(eff, 0) + 1
    probe_count: dict[int | None, int] = {}
    for _pid, site_id in ctx.probe_site.items():
        eff = ctx.explicit(site_id) or ctx.default_id
        probe_count[eff] = probe_count.get(eff, 0) + 1
    rows = (await db.execute(select(Site).order_by(Site.id))).scalars().all()
    return [{
        "id": s.id,
        "name": s.name,
        "is_default": s.id == ctx.default_id,
        "host_count": host_count.get(s.id, 0),
        "probe_count": probe_count.get(s.id, 0),
        "created_at": iso(s.created_at),
    } for s in rows]


@router.post("", summary="Create a site", status_code=201)
async def create_site(request: Request, db: AsyncSession = Depends(get_db),
                      _key: ApiKey = Depends(require_admin)):
    body = await request.json()
    name = site_svc.clean_name(body.get("name") if isinstance(body, dict) else None)
    if await _name_taken(db, name):
        raise HTTPException(409, "a site with this name exists")
    site = Site(name=name)
    db.add(site)
    await db.flush()
    await log_action(db, request, "site.create", "site", site.id, name)
    await db.commit()
    return {"id": site.id, "name": site.name, "is_default": False, "host_count": 0, "probe_count": 0,
            "created_at": iso(site.created_at)}


@router.patch("/{site_id}", summary="Rename a site")
async def rename_site(site_id: int, request: Request, db: AsyncSession = Depends(get_db),
                      _key: ApiKey = Depends(require_admin)):
    site = await db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    body = await request.json()
    name = site_svc.clean_name(body.get("name") if isinstance(body, dict) else None)
    if await _name_taken(db, name, exclude_id=site_id):
        raise HTTPException(409, "a site with this name exists")
    old = site.name
    site.name = name
    await log_action(db, request, "site.rename", "site", site.id, name, details={"from": old, "to": name})
    await db.commit()
    return {"id": site.id, "name": site.name}


@router.delete("/{site_id}", summary="Delete a site; its members move to the default site")
async def delete_site(site_id: int, request: Request, db: AsyncSession = Depends(get_db),
                      _key: ApiKey = Depends(require_admin)):
    site = await db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    default_id = await site_svc.default_site_id(db)
    if site_id == default_id or default_id is None:
        raise HTTPException(409, "the default site cannot be deleted")
    moved: dict[str, int] = {}
    for model in SITE_TABLES:
        res = await db.execute(update(model).where(model.site_id == site_id).values(site_id=default_id))
        moved[model.__tablename__] = res.rowcount or 0
    await db.delete(site)
    await log_action(db, request, "site.delete", "site", site_id, site.name,
                     details={"moved_to": default_id, "moved": moved})
    await db.commit()
    return {"ok": True, "moved_to": default_id, "moved": moved}
