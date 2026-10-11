"""Multi-tenancy (enterprise, phase 9): more than one tenant on one installation.

The isolation itself — tenant context, session filter, RLS, ClickHouse
predicates — is core (AGPL). This package adds what is only valuable once
there is more than one tenant (spec "Open Core boundary"):

* :mod:`.backend` — ``MultiTenancy``, the tenancy backend: memberships of
  every active tenant, support access (``via = support``), the operator
  flag, the last used tenant at login;
* :mod:`.lifecycle` — create, suspend, resume, schedule deletion (30 days
  suspended, owner decision 6), plans;
* :mod:`.purge` — the purge of a tenant's PostgreSQL and ClickHouse rows
  (resumable, audited, crypto-shred of its data keys first);
* :mod:`.plans` — fixed retention tiers (owner decision 5), the core's
  retention policy;
* :mod:`.support` — time-limited support access (owner decision 8);
* :mod:`.api` — the HTTP routes; :mod:`.jobs` — the scheduled purge and
  support-grant expiry.

Licence (feature ``multi_tenancy``, ``max_tenants``): creating tenants and
the lifecycle admin actions need it; on expiry existing tenants keep being
monitored and reachable (owner decision 9), only the admin actions stop.
"""
from extensions import Registry

FEATURE = "multi_tenancy"


def register(registry: Registry) -> None:
    from nodeglow_ee.tenancy import api, backend, jobs, plans

    registry.set_tenancy_backend(backend.MultiTenancy())
    registry.set_retention_policy(plans.retention_policy)
    registry.add_router(api.router)
    registry.add_scheduler_hook(jobs.schedule)
    registry.enable_feature(FEATURE)
