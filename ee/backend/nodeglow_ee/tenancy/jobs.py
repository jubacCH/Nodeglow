"""Scheduled installation jobs of the multi-tenancy package.

* :func:`run_tenant_purges` (every 15 minutes) purges the tenants whose
  deletion is due, one after another; a failure is logged and retried on
  the next run (the purge is resumable). Runs whatever the licence says:
  the deletion was promised (nDSG) when it was scheduled.
* :func:`run_support_expiry` (every minute) audits the end of support
  grants that have run out. Access itself ends at ``expires_at`` on every
  request; the job only records it.
"""
from __future__ import annotations

import logging

from services.metrics import instrument_job
from services.tenancy import system_job

log = logging.getLogger("nodeglow.ee.tenancy.jobs")

PURGE_JOB_ID = "ee_tenant_purges"
SUPPORT_JOB_ID = "ee_support_expiry"


@instrument_job("ee_tenant_purges")
@system_job("installation job: purge tenants whose deletion is due")
async def run_tenant_purges() -> dict[int, str]:
    from models import base as _base

    from nodeglow_ee.tenancy import lifecycle, purge

    async with _base.AsyncSessionLocal() as db:
        due = await lifecycle.due_for_purge(db)
    out: dict[int, str] = {}
    for tenant_id in due:
        try:
            await purge.purge_tenant(tenant_id)
            out[tenant_id] = "purged"
        except purge.PurgeRefused as exc:
            out[tenant_id] = f"refused: {exc}"
            log.warning("Purge of tenant %s refused: %s", tenant_id, exc)
        except Exception as exc:  # noqa: BLE001 — one tenant's failure must not stop the next
            out[tenant_id] = f"error: {exc}"
            log.error("Purge of tenant %s failed, retried next run", tenant_id, exc_info=True)
    return out


@instrument_job("ee_support_expiry")
@system_job("installation job: audit expired support access")
async def run_support_expiry() -> int:
    from nodeglow_ee.tenancy import support

    return await support.expire_grants()


async def schedule(scheduler) -> None:
    """Scheduler hook."""
    scheduler.add_job(run_tenant_purges, "interval", minutes=15, id=PURGE_JOB_ID,
                      replace_existing=True, max_instances=1, coalesce=True)
    scheduler.add_job(run_support_expiry, "interval", minutes=1, id=SUPPORT_JOB_ID,
                      replace_existing=True, max_instances=1, coalesce=True)
