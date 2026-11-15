"""Sites: network locations inside a tenant (B-14, concept F phase 3, PR 4).

docs/specs/2026-11-15-concept-f-phase-3-design.md, part C. A site is a name
and the hosts, probes, install tokens and scan schedules in it. Sites only
group and filter; syslog matching per site is out of scope.

**The effective site of a host** (owner decision 4):

1. the site set on the host itself;
2. else the site of the probe that checks it;
3. else the tenant's default site.

Revision 040 backfilled ``site_id = <default>`` on every existing host and
agent, so a stored default cannot be told apart from "never chosen". The
default site therefore counts as *not set*: a host stored in the default
site follows its probe. ``NULL`` means the same.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import func, select

from models.agent import Agent
from models.tenant import Site

MAX_NAME = 128


@dataclass
class SiteContext:
    """What it takes to compute effective sites for one tenant, read once."""
    default_id: int | None
    sites: dict[int, str] = field(default_factory=dict)
    probe_site: dict[int, int | None] = field(default_factory=dict)

    def explicit(self, site_id: int | None) -> int | None:
        """A stored site that counts as chosen (the default does not)."""
        return site_id if site_id is not None and site_id != self.default_id and site_id in self.sites else None

    def effective(self, host_site_id: int | None, probe_id: int | None) -> int | None:
        own = self.explicit(host_site_id)
        if own is not None:
            return own
        if probe_id is not None:
            inherited = self.explicit(self.probe_site.get(probe_id))
            if inherited is not None:
                return inherited
        return self.default_id


async def default_site_id(db) -> int | None:
    """The tenant's default site: the tenancy backend's, else its oldest site."""
    from services import tenancy

    tid = tenancy.session_tenant_id(db)
    sid = tenancy.backend().default_site_id(tid)
    if sid is not None:
        return sid
    return (await db.execute(select(func.min(Site.id)))).scalar_one_or_none()


async def load_context(db) -> SiteContext:
    sites = dict((await db.execute(select(Site.id, Site.name))).all())
    probes = dict((await db.execute(select(Agent.id, Agent.site_id).where(Agent.is_probe == True))).all())  # noqa: E712
    return SiteContext(default_id=await default_site_id(db), sites=sites, probe_site=probes)


async def resolve(db, value) -> int | None:
    """A site id from a request body: ``None`` (follow the probe / default) or an existing site."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(400, "site_id must be a site id or null")
    try:
        sid = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "site_id must be a site id or null") from None
    if await db.get(Site, sid) is None:
        raise HTTPException(400, "site_id does not refer to a site")
    return sid


def clean_name(value) -> str:
    name = (value or "").strip() if isinstance(value, str) else ""
    if not name:
        raise HTTPException(400, "name is required")
    if len(name) > MAX_NAME:
        raise HTTPException(400, f"name is longer than {MAX_NAME} characters")
    return name
