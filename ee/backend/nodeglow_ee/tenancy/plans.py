"""Retention plan tiers (owner decision 5: fixed tiers, not free values).

A tenant's plan (``tenants.plan``) caps every retention setting of the tenant
(``services.settings_registry.RETENTION_KEYS``) at the tier's days, on top of
the installation's ``<key>_max``. The core asks through
``extensions.registry.retention_policy`` (:func:`retention_policy`), in the
settings form (the tenant admin can only choose within the plan) and in the
nightly cleanup. A tenant without a plan (``NULL``, the default tenant of an
existing install) has no plan cap.

Data whose retention is fixed by the storage, not by a setting, is not
extended by a larger tier: ClickHouse keeps ``ping_checks`` 30 days, agent
and bandwidth metrics 7 days, syslog per its severity TTLs (spec "Retention
per tenant"). A tier is a ceiling, never a promise beyond those.
"""
from __future__ import annotations

from dataclasses import dataclass

from models.tenant import Tenant


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    retention_days: int

    def public(self) -> dict:
        return {"key": self.key, "name": self.name, "retention_days": self.retention_days}


PLANS: dict[str, Plan] = {
    p.key: p for p in (
        Plan("standard", "Standard", 30),
        Plan("professional", "Professional", 90),
        Plan("enterprise", "Enterprise", 365),
    )
}
DEFAULT_PLAN = "standard"


def get_plan(key: str | None) -> Plan | None:
    return PLANS.get(key or "")


def plan_list() -> list[dict]:
    return [p.public() for p in PLANS.values()]


async def retention_policy(db, tenant_id: int, key: str) -> int | None:
    """Registered as the core's retention policy: the tenant's tier days."""
    tenant = await db.get(Tenant, tenant_id)
    plan = get_plan(tenant.plan if tenant is not None else None)
    return plan.retention_days if plan is not None else None


async def retention_reductions(db, tenant_id: int, plan: Plan) -> dict[str, dict]:
    """What assigning ``plan`` would lower: ``{key: {"from": stored, "to":
    days}}`` for every stored retention value above the plan ("keep
    forever", 0, included). Writes nothing. Runs in the tenant's scope."""
    from models.settings import get_setting
    from services.settings_registry import RETENTION_KEYS
    from services.tenancy import tenant_scope

    out: dict[str, dict] = {}
    with tenant_scope(tenant_id):
        for key in RETENTION_KEYS:
            raw = await get_setting(db, key, None)
            try:
                value = int(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                value = None
            if value is None:
                continue
            if value <= 0 or value > plan.retention_days:
                out[key] = {"from": value, "to": plan.retention_days}
    return out


async def clamp_tenant_retention(db, tenant_id: int, plan: Plan) -> dict[str, str]:
    """Lower the tenant's stored retention values to the plan (the settings
    form shows what is in effect). Runs in the tenant's scope. Returns the
    changed keys with their new value. Does not commit."""
    from models.settings import set_setting
    from services.settings_registry import RETENTION_KEYS
    from services.tenancy import tenant_scope

    changed: dict[str, str] = {}
    lowered = await retention_reductions(db, tenant_id, plan)
    with tenant_scope(tenant_id):
        for key in RETENTION_KEYS:
            if key in lowered:
                changed[key] = str(lowered[key]["to"])
                await set_setting(db, key, changed[key], commit=False)
    return changed
