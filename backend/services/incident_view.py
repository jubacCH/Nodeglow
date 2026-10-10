"""How incidents are filtered and presented, shared by /api/v1 and /api/v2.

One definition of "open" (``open`` or ``acknowledged``) and one way to attach
the affected hosts, so the incident list, the dashboard and the sidebar badge
cannot drift apart.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from models.incident import Incident, IncidentEvent
from services import host_state as hs

STATUSES = ("open", "acknowledged", "resolved")
OPEN_STATUSES = ("open", "acknowledged")
SEVERITIES = ("critical", "warning", "info")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

# At most this many host summaries per incident in list views; host_count
# always carries the full number.
MAX_HOSTS_PER_ITEM = 20


def host_id_condition(host_id: int):
    """SQL condition: the incident's recorded host_ids contain ``host_id``.

    host_ids is a compact JSON list ("[3,17]"), so the four LIKE shapes cover
    first, middle, last and only element without matching 1 inside 17. Works
    the same on PostgreSQL and SQLite. The ", " variants tolerate a list
    written with Python's default separators.
    """
    h = int(host_id)
    col = Incident.host_ids
    return or_(
        col == f"[{h}]",
        col.like(f"[{h},%"),
        col.like(f"%,{h},%"),
        col.like(f"%,{h}]"),
        col.like(f"%, {h},%"),
        col.like(f"%, {h}]"),
    )


def build_conditions(
    *,
    statuses: set[str] | None = None,
    severities: set[str] | None = None,
    rule: str | None = None,
    search: str | None = None,
    host_name: str | None = None,
    host_id: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list:
    conds = []
    if statuses:
        conds.append(Incident.status.in_(sorted(statuses)))
    if severities:
        conds.append(Incident.severity.in_(sorted(severities)))
    if rule:
        conds.append(Incident.rule == rule)
    if search:
        conds.append(Incident.title.ilike(f"%{search}%"))
    if host_name:
        conds.append(Incident.id.in_(
            select(IncidentEvent.incident_id).where(IncidentEvent.summary.ilike(f"%{host_name}%"))
        ))
    if host_id is not None:
        conds.append(host_id_condition(host_id))
    if created_from is not None:
        conds.append(Incident.created_at >= created_from)
    if created_to is not None:
        conds.append(Incident.created_at < created_to)
    return conds


async def host_summaries(db, incidents, *, now: datetime | None = None,
                         limit_per_item: int | None = MAX_HOSTS_PER_ITEM,
                         states: dict | None = None) -> dict[int, list[dict]]:
    """{incident_id: [{id, name, hostname, state, state_reason}]} — batched.

    One host query and one state evaluation for the union of all hosts.
    Incidents without recorded hosts are absent from the result. Pass
    ``states`` when the caller already evaluated all hosts.
    """
    from models.ping import PingHost

    wanted: dict[int, list[int]] = {}
    union: set[int] = set()
    for inc in incidents:
        ids = hs.parse_host_ids(inc.host_ids)
        if ids is None:
            continue
        ids = ids if limit_per_item is None else ids[:limit_per_item]
        wanted[inc.id] = ids
        union.update(ids)
    if not wanted:
        return {}
    hosts = (await db.execute(select(PingHost).where(PingHost.id.in_(union)))).scalars().all() if union else []
    by_id = {h.id: h for h in hosts}
    if states is None:
        states = await hs.host_states(db, hosts, now, topology=None) if hosts else {}
    out: dict[int, list[dict]] = {}
    for inc_id, ids in wanted.items():
        items = []
        for hid in ids:
            h = by_id.get(hid)
            if h is None:
                items.append({"id": hid, "name": None, "hostname": None,
                              "state": None, "state_reason": "Host deleted"})
                continue
            st = states.get(hid)
            items.append({
                "id": h.id, "name": h.name, "hostname": h.hostname,
                "state": st.state if st else None,
                "state_reason": st.reason if st else None,
            })
        out[inc_id] = items
    return out


def host_fields(inc, summaries: dict[int, list[dict]]) -> dict:
    """host_ids / host_count / hosts for one incident (None = not recorded)."""
    ids = hs.parse_host_ids(inc.host_ids)
    return {
        "host_ids": ids,
        "host_count": len(ids) if ids is not None else None,
        "hosts": summaries.get(inc.id) if ids is not None else None,
    }
