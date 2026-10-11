# Multi-tenancy

Status: design · 2026-10-10 — phases 1–4, 6 and 7 implemented 2026-10-11 (see "Implementation status")

## Why

Nodeglow is going to be run two ways: as a Swiss-hosted SaaS, and as on-prem
installs at customers and MSPs. Both need one installation to hold several
customers whose data never meets. Until that exists, every customer gets their
own instance — which works, and is what we do in the meantime, but it does not
scale operationally (one stack, one upgrade, one backup per customer) and it
gives an MSP no single view across its customers.

Today the codebase has no notion of "whose" anything. Every table is global,
`settings` is one key/value bag for the whole installation, `users.role` is one
role for everything, the WebSocket hub broadcasts to every connected client, and
several in-process caches are keyed by values that are only unique inside one
network (`source_ip`, a Proxmox `cluster_name`, an alert title). Remote probes
(`2026-08-28-remote-probes-design.md`) let one instance *see* several sites;
they deliberately left isolation to this piece of work.

Owner decisions this design builds on (2026-10-10):

- SaaS (Swiss hosting) **and** on-prem; same code base.
- Real multi-tenancy is built in parallel; until it ships, one instance per
  customer.
- Open Core: core under AGPL-3.0; multi-tenancy / MSP portal, SSO/SCIM, SLA
  reports, HA and AI premium live in a commercially licensed `ee/` directory.
- AI only with an explicit per-customer opt-in.

## Goals

- One installation serves many customers with **hard data isolation**: no
  request, job, cache, stream or export can return one tenant's data to another
  tenant's users, even when a route forgets a filter.
- MSP staff get a cross-tenant view (fleet overview, incidents across
  customers, switching into a customer) without sharing credentials.
- Customer users see only their own tenant, with the existing three roles.
- A user can belong to several tenants with a different role in each.
- Core (AGPL) keeps working exactly as today with one tenant; everything that
  makes a second tenant possible is in `ee/`.
- Existing installs upgrade in place; existing single-customer instances can
  later be moved into a multi-tenant SaaS without re-enrolling by hand.

## Non-goals

- **Database-per-tenant or schema-per-tenant.** Rejected below; shared tables
  with a `tenant_id` are the model.
- **Probe-side integrations and probe-side subnet scans.** Still a separate
  piece of work (see "SaaS reachability" — it is a blocker for SaaS, but not for
  isolation).
- **Horizontal sharding of tenants across backend workers.** The scheduler
  stays leader-elected and single-active; fairness is solved inside one process
  first.
- **Billing.** Plan limits are modelled (quotas), invoicing is not.
- **Per-tenant encryption keys.** Listed as an option for later (crypto-shred on
  tenant deletion); one `SECRET_KEY` remains.

## Tenant model

```
installation (operator)         one per deployment; the people running it
└── provider                    an MSP; exactly one in on-prem installs
    └── tenant                  a customer — THE isolation boundary
        └── site (optional)     a network location inside a customer
```

- **Tenant** is the only isolation boundary. Every tenant-owned row carries
  `tenant_id`; nothing is ever shared between tenants.
- **Provider** is an access grouping, not an isolation boundary. It exists so
  the SaaS can host several MSPs, each seeing *its* customers — and so a direct
  SaaS customer is just a provider with one tenant. On-prem there is one
  provider (id 1) and nobody has to know it exists.
- **Site** groups hosts, probes and scans inside a tenant. It is not an
  isolation boundary, but it is where an IP address becomes unambiguous: two
  branch offices of the same customer can both be `192.168.1.0/24`. Probes
  belong to a site; syslog and host matching happen per site (see "Syslog").
- **Installation operator** is a flag on the user (`users.is_operator`), not a
  membership. Operators manage providers and installation settings; they have
  no implicit read access to tenant data — they get it by being made a member,
  which is audited like any other grant.

### New tables

```
providers           id, name, slug, created_at
tenants             id, provider_id FK, name, slug (unique per provider),
                    status (active|suspended|deleting), plan, created_at
sites               id, tenant_id FK, name, created_at
tenant_memberships  user_id FK, tenant_id FK, role (admin|editor|readonly),
                    created_at, created_by          PK (user_id, tenant_id)
provider_memberships user_id FK, provider_id FK,
                    role (msp_admin|msp_operator|msp_readonly)
tenant_settings     tenant_id FK, key, value, encrypted   PK (tenant_id, key)
user_preferences    user_id FK, tenant_id FK, key, value  PK (user_id, tenant_id, key)
```

Ids stay globally unique serials. Per-tenant id sequences would make every
ClickHouse join ambiguous (`host_id` is unique across the installation today and
ClickHouse rows rely on that), and would buy nothing but prettier URLs.

### Roles

- Tenant roles are today's roles, moved from `users.role` to
  `tenant_memberships.role`. The middleware's checks in `main.py`
  (`inject_globals`: read-only on every mutating path, `/api/users` admin-only)
  stay, but read the role of the **active tenant**.
- Provider roles give an effective role in every tenant of that provider:
  `msp_admin` → admin, `msp_operator` → editor, `msp_readonly` → readonly. An
  explicit tenant membership overrides it in either direction, so an MSP can
  give a technician read-only access to a sensitive customer.
- Customer users only ever have tenant memberships. A customer admin manages
  users and settings of their own tenant and nothing above it.

## Data isolation — Postgres

### Table classification

Every table in `models/` (30, plus `alembic_version`):

| Table | Class | Notes |
|---|---|---|
| `settings` | installation | stays as is; tenant keys move to `tenant_settings` (see "Settings split") |
| `users` | global | identities are global so one person can be member of several tenants |
| `sessions` | per-user | gains `active_tenant_id` |
| `ping_hosts` | tenant | gains `site_id`; `parent_id` and `probe_id` must stay inside the tenant |
| `integration_configs` | tenant | `cluster_group` dedupe must be per tenant (see below) |
| `snapshots` | tenant | hot path; `entity_id` is a soft ref to `integration_configs`; `tenant_id` denormalised |
| `syslog_views` | tenant | saved filters |
| `incidents` | tenant | `host_ids_hash` dedupe per tenant |
| `incident_events` | tenant | child of `incidents`, `tenant_id` denormalised |
| `log_templates` | tenant | `template_hash` unique → `(tenant_id, template_hash)`; `example` is a real log line |
| `host_baselines` | tenant | `host_key` is a `source_ip` today → `(tenant_id, site_id, host_key, …)` |
| `precursor_patterns` | tenant | child of `log_templates` |
| `fleet_patterns` | tenant | "fleet" means the tenant's fleet; `source_ips` is a CSV of IPs |
| `agents` | tenant | gains `site_id`; unique `lower(hostname)` → `(tenant_id, lower(hostname))` |
| `agent_install_tokens` | tenant | gains `site_id` (enrolment lands the agent in that site) |
| `subnet_scan_schedules` | tenant | gains `site_id` |
| `subnet_scan_logs` | tenant | child |
| `credentials` | tenant | encrypted SNMP/WinRM/SSH secrets |
| `snmp_mibs` | global | MIB catalogue; uploads are operator-only in multi-tenant mode |
| `snmp_oids` | global | parsed from MIBs |
| `snmp_host_configs` | tenant | child of `ping_hosts`; `credential_id` must be same tenant |
| `snmp_results` | tenant | child of `ping_hosts` |
| `api_keys` | tenant | one key = one tenant; provider-scoped keys are an `ee` addition |
| `notification_logs` | tenant | |
| `alert_rules` | tenant | `source_id` is a soft ref to `integration_configs` |
| `discovered_ports` | tenant | child of `ping_hosts` |
| `audit_logs` | tenant, nullable | NULL = installation/provider-level action (see "Audit") |
| `ai_usage_logs` | tenant | basis for per-tenant AI cost and quotas |
| `backup_jobs` | tenant | `source_config_id` soft ref |
| `backup_history` | tenant | child |

Rules that follow from the table:

1. **`tenant_id` on every tenant-owned table, including children.** It is
   tempting to derive it through the parent (`incident_events` → `incidents`),
   but RLS policies with joins are slow and easy to get wrong, and the hot
   paths (`snapshots`, `incident_events`) are exactly the ones that must not
   join. `NOT NULL`, indexed, and the leading column of every existing
   composite index that a tenant-scoped query uses
   (`ix_snap_type_entity_ts` → `(tenant_id, entity_type, entity_id, timestamp DESC)`,
   `ix_incident_status` → `(tenant_id, status)`, …).
2. **Uniqueness is per tenant.** Every global unique constraint on a
   tenant-owned table becomes `(tenant_id, …)`: `ix_agents_hostname_lower`,
   `log_templates.template_hash`, `ix_baseline_host_time`,
   `ix_disc_port_host_port`, `ix_precursor_tpl_event`. Exceptions that stay
   global on purpose: `agents.token`, `api_keys.key_hash`,
   `agent_install_tokens.token_hash` — they are looked up *before* a tenant is
   known, and the row they find is what tells us the tenant.
3. **Cross-tenant references are impossible at the schema level.** Each real
   FK becomes composite: parents get `UNIQUE (tenant_id, id)`, children
   reference `(tenant_id, parent_id)`. Then `ping_hosts.probe_id` cannot point
   at another customer's probe, and `snmp_host_configs.credential_id` cannot use
   another customer's credential, whatever the application does. Postgres 16
   supports `ON DELETE SET NULL (probe_id)` with a column list, so the
   `fk_ping_hosts_probe_id` semantics from migration 034 survive (nulling only
   `probe_id`, not `tenant_id`).
4. **Soft references are validated in one place.** `snapshots.entity_id`,
   `alert_rules.source_id`, `backup_jobs.source_config_id` and
   `audit_logs.target_id` have no FK. Writes to them go through a helper that
   loads the target under the current tenant context and refuses otherwise.

### Enforcement: two layers

**Layer 1 — application filter (primary).** A `TenantScoped` mixin on every
tenant-owned model, and a tenant context (`contextvars.ContextVar`) set once per
request and once per job iteration. On the session:

- `do_orm_execute` adds `with_loader_criteria(TenantScoped, lambda cls:
  cls.tenant_id == ctx.tenant_id, include_aliases=True)` to every ORM
  statement. That covers `select()`, relationship loads, and ORM-enabled
  `update()`/`delete()` (SQLAlchemy 2.1, which `requirements.txt` pins).
- `before_flush` stamps `tenant_id` on new objects and **raises** if an object
  carries a different one.
- No context and no explicit `system_scope()` → the statement raises. Failing
  closed is the point: a background job that forgot to set a tenant must crash
  in tests, not silently read everybody.

What it does not cover: `text()` SQL (`routers/system.py` has eleven raw
statements, also `services/digest.py`, `services/postmortem.py`,
`services/backup.py`, `services/snmp.py`, `scheduler.py`) and Core-level
statements against `Table` objects. Those are the reason for layer 2.

**Layer 2 — Postgres Row Level Security (defence in depth).** On every
tenant-owned table:

```sql
ALTER TABLE ping_hosts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ping_hosts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ping_hosts
  USING      (tenant_id = current_setting('nodeglow.tenant_id', true)::int)
  WITH CHECK (tenant_id = current_setting('nodeglow.tenant_id', true)::int);
```

The application sets the GUC per transaction from the same context
(`after_begin` → `SELECT set_config('nodeglow.tenant_id', :t, true)`), so it is
transaction-local and cannot leak through the asyncpg pool. An unset GUC yields
NULL and therefore zero rows — fail closed again.

This needs a role split, because table owners bypass RLS and today one
`nodeglow` user owns everything and runs the app:

- `nodeglow_owner` — owns the schema, runs Alembic (`migrate.py`, the update
  orchestrator's `migrate` step).
- `nodeglow_app` — runtime role, no `BYPASSRLS`.
- `nodeglow_system` — `BYPASSRLS`, used only by the few code paths that are
  genuinely cross-tenant (tenant lifecycle, export, retention sweeps, the MSP
  overview aggregate), each wrapped in `system_scope()` and audited.

**Evaluation.**

| | App filter | RLS |
|---|---|---|
| Catches a forgotten `.where()` on ORM queries | yes | yes |
| Catches raw `text()` SQL | no | yes |
| Catches a job with no tenant context | yes (raises) | yes (0 rows) |
| Works in the SQLite test suite (`tests/conftest.py`) | yes | no |
| Gives correct *results* (not just safe ones) | yes | only if context is right |
| Cost | negligible | small; equality predicate on an indexed leading column |
| Operational burden | none | role split, migrations must run as owner |

**Recommendation: both.** The application filter is the mechanism the code is
written against — it is what makes queries correct and it runs in every unit
test. RLS is the net under it for the raw SQL and for the bug nobody has written
yet. Neither alone is enough: RLS alone turns every missing context into an
empty page that looks like "no data" (a false green, the failure this codebase
already fears most); the filter alone does not see `text()`.

Both layers ship in **core**, also for single-tenant installs. One schema and
one code path everywhere is cheaper than two, and it means the isolation
mechanism is exercised by every install, not only by the few that pay.

### Rejected alternatives

- **Database per tenant.** Strongest isolation, but N Alembic runs per upgrade,
  N connection pools, and the MSP overview becomes N queries. Contradicts the
  update orchestrator, which migrates one database.
- **Schema per tenant** (`search_path`). Same migration fan-out, plus every
  cached plan and every `create_all` path (`migrate.py` builds `agents` via
  `create_all`, not a migration) becomes per schema.

## Data isolation — ClickHouse

Four tables in `clickhouse/init.sql` / `_PHASE2_SCHEMAS`: `syslog_messages`,
`ping_checks`, `agent_metrics`, `bandwidth_metrics`. About a hundred call sites
use `services/clickhouse_client.py` helpers or raw `query()` strings, eleven
files query `syslog_messages` directly. There is no ORM to hang a filter on.

### Column and sort key

Every table gains `tenant_id UInt32` as the first column of `ORDER BY`:

```
syslog_messages    ORDER BY (tenant_id, severity, source_ip, timestamp)
ping_checks        ORDER BY (tenant_id, host_id, timestamp)
agent_metrics      ORDER BY (tenant_id, agent_id, timestamp)
bandwidth_metrics  ORDER BY (tenant_id, source_type, source_id, interface_name, timestamp)
```

`syslog_messages` additionally gains `site_id UInt32 DEFAULT 0`, because
`source_ip` alone is ambiguous inside a tenant with several sites.
`bandwidth_metrics.source_id` is a String holding agent ids, config ids or a
device MAC — a MAC is not unique across customers, which is one more reason the
tenant must be part of the key.

### Enforcement

1. **Helpers take the tenant.** Every helper in `clickhouse_client.py` reads the
   tenant context and adds `tenant_id = {tenant_id:UInt32}`; `_where_clauses`
   adds it first. `query()`/`query_scalar()`/`query_chunked()` keep their
   signature but refuse to run without a context unless called in
   `system_scope()`.
2. **Row policies as the net**, the ClickHouse equivalent of RLS:

   ```sql
   CREATE ROW POLICY tenant_isolation ON nodeglow.syslog_messages
     FOR SELECT USING tenant_id = toUInt32(getSetting('SQL_nodeglow_tenant'))
     TO nodeglow_app;
   ```

   with `<custom_settings_prefixes>SQL_</custom_settings_prefixes>` in
   `clickhouse/config.xml`, and the client passing
   `settings={'SQL_nodeglow_tenant': ctx}` on every query. A query without the
   setting errors instead of returning everything. A second user
   `nodeglow_system` without the policy serves `system_scope()`. Today the app
   connects as one user from `CLICKHOUSE_URL`; this becomes two DSNs.
   *To verify in the spike:* `getSetting` on a custom setting inside a row
   policy on 24.8, and that `clickhouse-connect` per-query settings reach it.
3. **Inserts** stamp `tenant_id` in `insert_batch`, `insert_ping_checks`,
   `insert_agent_metrics`, `insert_bandwidth_metrics` from the row, never from
   ambient context — ingestion paths (syslog, agent reports) handle several
   tenants in one batch.
4. **Noisy neighbour in ClickHouse.** `CREATE QUOTA … KEYED BY client_key` with
   the client sending `quota_key = tenant_id`, plus a settings profile for
   `max_execution_time` / `max_memory_usage`. One customer's 90-day full-text
   search must not stall everybody's dashboard.

### Migrating existing tables

Correctness and performance are separable, and the migration exploits that:

**Step A — correctness, instant.** Through the existing `_PHASE3_ALTERS` path:

```sql
ALTER TABLE syslog_messages ADD COLUMN IF NOT EXISTS tenant_id UInt32 DEFAULT 1;
ALTER TABLE syslog_messages ADD COLUMN IF NOT EXISTS site_id   UInt32 DEFAULT 0;
-- same for ping_checks, agent_metrics, bandwidth_metrics
```

A metadata-only change; every existing row reads as tenant 1. With the helpers
filtering and the row policies in place, isolation is complete from here on —
just without a tenant prefix in the sort key, so a tenant-scoped scan reads
other tenants' granules. Irrelevant with one tenant, which is every install
today.

**Step B — performance, a rebuild.** ClickHouse can only *append* newly added
columns to `ORDER BY` (`MODIFY ORDER BY`), not prepend. A prefix means a new
table:

1. `CREATE TABLE syslog_messages_v2 … ORDER BY (tenant_id, …)`, same TTL and
   settings (`ttl_only_drop_parts = 0` for syslog, `1` for the others).
2. Copy partition by partition, oldest first:
   `INSERT INTO syslog_messages_v2 SELECT * FROM syslog_messages WHERE _partition_id = '…'`.
   Progress is persisted, so it resumes after a restart.
3. Cut over: pause the syslog flush (the UDP/TCP queues buffer), copy the
   current partition's remainder, `EXCHANGE TABLES syslog_messages AND
   syslog_messages_v2` (the `nodeglow` database is Atomic, so the swap is
   atomic), resume, drop the old table after a grace day.

Volumes, from the estimates in `init.sql`: `ping_checks` ~144k rows/day per 100
hosts × 30 days TTL ≈ 4–5M rows; `agent_metrics` ~144k/day per 50 agents × 7
days ≈ 1M; `bandwidth_metrics` similar order. Seconds each. `syslog_messages` is
the only one that matters: up to 180 days for low-noise rows, and its size is
whatever the customer's devices send — measure with `system.parts` first. The
job needs free disk about the size of the table being copied; the preflight
refuses otherwise, like the update orchestrator's 2 GB check.

Step B is required only before a second tenant exists. Core ships it as a
background job that single-tenant installs may run whenever convenient; `ee`
refuses to create tenant #2 until it has completed.

### Retention per tenant

TTL is a table property, so "customer A keeps syslog 30 days, B 365" cannot be a
setting read at cleanup time. Option: a `retention_days UInt16` column stamped at
insert from the tenant's plan, and `TTL toDateTime(timestamp) +
toIntervalDay(retention_days)` alongside the severity rules. Needs a spike
(interaction with `ttl_only_drop_parts = 1` on the daily-partitioned tables,
which would keep a part until its longest-lived row expires). Fallback: a small
set of plan tiers. Note in passing that `ping_retention_days` and
`proxmox_retention_days` are already offered in settings but have no effect on
ClickHouse, whose TTLs are fixed in the DDL — this work should fix that rather
than replicate it per tenant.

## Settings split

`settings` stays and becomes **installation-only**. Tenant-scoped keys move to
`tenant_settings`; per-user ones to `user_preferences`. Each key is declared
once in a registry (`services/settings_registry.py`: key → scope, default,
encrypted, optional installation-level cap), and `get_setting` resolves
`tenant → installation → default` for keys declared as overridable. Asking for a
tenant-scoped key without a tenant context raises — the same fail-closed rule.

| Key(s) | Scope | Notes |
|---|---|---|
| `setup_complete` | installation | |
| `syslog_port` | installation | one listener per installation (see "Syslog") |
| `agent_server_url` | installation | what agents are told to call |
| `nodeglow_ip` | installation | Proxmox helper; meaningless across sites, revisit with probe-side integrations |
| `geoip_enabled`, `geoip_license_key`, `geoip_last_updated` | installation | one MaxMind DB for all |
| `disk_usage_history` | installation | self-monitoring |
| `_lockout:<username>` | installation | users are global; lockout must be too |
| `agent_enrollment_key` | installation (legacy) | removal candidate; shared key cannot carry a tenant |
| `site_name`, `timezone` | tenant | `site_name` becomes the tenant display name; installation gets a separate brand name |
| `notify_enabled`, `notify_grace_minutes`, `notify_*_min_severity` | tenant | |
| `telegram_*`, `discord_webhook_url`, `webhook_url`, `webhook_secret` | tenant | |
| `smtp_*` | tenant, with installation fallback | SaaS: installation relay by default, tenant may bring its own |
| `digest_enabled`, `digest_day`, `digest_hour` | tenant | |
| `daily_ai_summary_*` | tenant | `_last_sent` is state, also per tenant |
| `ai_enabled` (new) | tenant | the opt-in; default off; consent recorded in the audit log |
| `claude_api_key` | installation, tenant override (`ee`) | SaaS: operator's key; on-prem MSP: may differ per customer |
| `ping_interval`, `proxmox_interval` | tenant, floor at installation | a tenant cannot ask for 1 s checks |
| `latency_threshold_ms`, `anomaly_threshold`, `proxmox_*_threshold` | tenant | |
| `*_retention_days` (5 keys) | tenant, capped at installation/plan | |
| `correlation_min_*`, `predictor_*` | tenant | |
| `syslog_allowlist_only` | tenant | |
| `phpipam_*` | tenant | really integration config; candidate to move into `integration_configs` |
| `ldap_*` | installation in core; per tenant/provider in `ee` via an identity-provider table | see "AuthN" |
| `dashboard_layout` | per user, per tenant | currently one layout for the whole installation |

The two in-process caches in `main.py` follow the split: `_settings_cache`
(site name, timezone) and `_nav_cache` (integration counts, pending tasks)
become dicts keyed by `tenant_id`, and their `invalidate_*` functions take one.

## AuthN / AuthZ

### Selecting the tenant

| Option | Verdict |
|---|---|
| Path (`/t/{slug}/…`) | Rejected. Every backend route and every rewrite in `frontend/next.config.mjs` changes; the route-auth test's path enumeration doubles. |
| Subdomain (`acme.nodeglow.ch`) | Optional convenience in SaaS only. Needs wildcard DNS/TLS, a per-host cookie decision, and is a burden on-prem. Never authoritative. |
| Header + session | **Recommended.** |

The rule:

1. `sessions.active_tenant_id` is the default tenant of a browser session, set
   at login (last used, or the only membership) and changed via
   `POST /api/auth/tenant`.
2. The frontend sends `X-Nodeglow-Tenant: <id>` on every request from its tab
   state. If present, it wins — so two tabs can show two customers. The
   middleware checks membership on every request; a tenant the user is not a
   member of is answered 404, not 403.
3. If a subdomain maps to a tenant, it only pre-selects; it must agree with the
   header/session or the request is refused.

`inject_globals` resolves the tenant right after the user, stores it in
`request.state.tenant` and the context var, and applies the role checks against
the membership role. The `/api/v1` key path resolves the tenant from the key.
Routes under `_AUTH_SKIP_PREFIXES` (`/api/agent/`, `/install/`) resolve it from
their own token.

### API keys

`api_keys.tenant_id NOT NULL`: a key belongs to one tenant and needs no header.
`require_api_key` in `routers/api_v1.py` sets the tenant context from the key.
The session fallback there (the virtual `ApiKey(id=0, …)`) uses the active
tenant. Provider-scoped keys for MSP automation are `ee`: `tenant_id NULL`,
`provider_id` set, header required, membership checked like a user.

### Agent and probe enrolment

- `agent_install_tokens` gain `tenant_id` and `site_id`. A token is the only
  thing that decides where a new agent lands; `POST /api/agent/enroll` takes the
  tenant from the token and never from the request body.
- The shared `agent_enrollment_key` flow (`_shared_enrollment_allowed`) cannot
  carry a tenant and is refused in multi-tenant mode.
- Re-enrolment by hostname (`routers/agents.py`, the case-insensitive lookup on
  `Agent.hostname`) is scoped to the token's tenant. Today two customers with a
  host called `srv01` would collide on `ix_agents_hostname_lower`; one would
  re-enrol into the other's agent row if it could prove the token.
- The auto-created `PingHost` for an enrolled agent is matched by hostname/IP
  only inside the agent's tenant **and site** — the current lookup by
  `agent_ip` across all hosts is exactly the RFC1918 collision.
- `/api/agent/report` and `/api/agent/logs` find the agent by token hash
  (globally unique) and run the rest of the request in the agent's tenant.
  Probe assignments (`_probe_assignments`) and results (`_record_probe_results`)
  are then naturally confined by the composite FK on `ping_hosts.probe_id`.

### Identity, LDAP, SSO

`users.username` stays globally unique for local accounts. Directory and SSO
users are identified by `(identity_provider_id, external_subject)`, because
`jdoe` at customer A and `jdoe` at customer B are different people. Core keeps
today's single LDAP config (installation-level settings, maps to the default
tenant). In `ee`, an `identity_providers` table (scope: provider or tenant;
type: ldap | oidc | saml) with group → membership-role mapping, plus SCIM for
provisioning. Login does home-realm discovery by e-mail domain.

In SaaS, per-tenant LDAP is mostly theoretical: the customer's AD sits in their
RFC1918 network, which the SaaS cannot reach. SaaS customers use OIDC/SAML
(Entra ID, Google); LDAP remains an on-prem feature.

### Audit

`audit_logs` gains `tenant_id` (nullable: NULL = installation or provider level,
e.g. "tenant created"), `provider_id`, and `acting_via` (`membership` |
`provider_role` | `operator` | `system`). `services/audit.py:log_action` takes
the tenant from context. Customer admins see their tenant's log, including
which MSP staff accessed it and how — that is the transparency a customer will
ask for. Tenant switches and every `system_scope()` entry are audited.

## Background processing

### Scheduler

`scheduler.py` registers ~20 global jobs under one leader lock. They split into
two kinds:

- **Data-plane jobs** — `run_ping_checks`, `run_integration_checks`,
  `run_snmp_polls`, `update_ssl_expiry`, `resolve_host_dns`,
  `run_port_discovery`, subnet scans. These iterate over *work items* (hosts,
  integration configs) whose tenant is known per item. They stay one global
  pass in `system_scope()`, set the tenant context per item before any write or
  notification, and get a **fair-share queue**: work is taken round-robin across
  tenants, with a per-tenant concurrency cap derived from the plan, inside the
  existing `_collect_semaphore`. A tenant with 2 000 hosts cannot push another
  tenant's 20 checks past their interval.
- **Analysis jobs** — `run_correlation`, `run_log_intelligence`,
  `run_log_analytics`, `run_alert_rules`, `run_backup_compliance`,
  `cleanup_old_results`, `run_weekly_digest`, `run_daily_ai_summary`. These
  become loops over active tenants, each iteration in its own tenant context,
  its own DB session, a time budget, and its own error handling: one tenant's
  exception is logged with its id and the loop continues. `instrument_job`
  metrics get a `tenant` label so the self-check can name the tenant whose
  correlation is stuck.
- **Installation jobs** stay global: `check_disk_space`,
  `cleanup_clickhouse_logs`, `update_geoip`, `cleanup_legacy_api_keys`,
  `run_self_check_job`.

Self-check incidents are installation health; they go to the operator's
notification channels (installation-level), and additionally into each affected
tenant when the failure affects their data (ClickHouse down affects all;
a stale probe affects one).

### In-process state that must be keyed by tenant

These are module-level and today assume one network:

| Where | State | Problem across tenants |
|---|---|---|
| `notifications.py` | `_recent` keyed by alert **title** | "Host down: router" at customer A suppresses the same alert at customer B for 5 min |
| `services/integration` / `scheduler.py` | `cluster_group` auto-populated from Proxmox `cluster_name` | two customers' clusters named `pve` are deduped as one HA group — one customer's data skipped |
| `services/log_intelligence.py` | `_template_cache`, `_template_counts`, `_new_templates`, `_burst_timestamps` keyed by template hash | counts and noise scores merge; `example` exposes another customer's log line |
| `services/syslog.py` | `_host_cache`, `_allowlist_ips`, `_rdns_cache`, `_ip_msg_counts` keyed by IP/hostname | overlapping RFC1918; wrong host attribution |
| `services/log_intelligence.py` | `compute_baselines` uses `source_ip` as `host_key` | same |
| `routers/dashboard.py` | `pred_svc._disk_pred_cache` (one dict, 5 min) | dashboard of tenant B serves tenant A's disk predictions |
| `main.py` | `_nav_cache`, `_settings_cache` | wrong counts / name |
| `services/correlation.py` | `_rule_hit_counts`, `_topo_cache` | keyed by global ids — safe, but key by tenant anyway for clarity |
| `services/rules.py` | `_consecutive_matches` by rule id | safe (global ids) |
| `services/ai_client.py` | `_semaphore = Semaphore(2)` | one tenant's postmortems block everyone's; needs per-tenant fairness and per-tenant usage caps |

The isolation tests (below) include one per row of this table.

### Syslog ingestion — the critical part

Syslog is UDP/TCP from devices that cannot authenticate. The only thing the
listener in `services/syslog.py` knows is the source address. Mapping that to a
tenant is the hard question, and the obvious answer is wrong:

- **Source-IP mapping** is ambiguous by construction. Customers use overlapping
  RFC1918 ranges; behind NAT the source is the customer's public IP, shared by
  every device; with Docker's userland proxy the source can even be the bridge
  gateway. Any table "IP → tenant" either collides or attributes to the wrong
  customer. Rejected as the primary mechanism.
- **Port per tenant** (`514 + n`) works on-prem for a handful of customers, but
  is unauthenticated (anyone who learns the port writes into that tenant), does
  not survive NAT/firewalls cleanly, and does not scale in SaaS. Acceptable only
  as an on-prem escape hatch.
- **TLS syslog (RFC 5425) with a client certificate per tenant** is correct but
  most network gear cannot do it.
- **Probe-based ingestion — recommended.** The probe (an agent with the probe
  capability, already enrolled into exactly one tenant and site) listens on
  UDP/TCP 514 *inside* the customer network and forwards batches over its
  existing authenticated channel. Tenant and site come from the probe's token;
  the original device address is carried per entry and is unambiguous within
  the site.

Concretely:

1. Agent: a syslog relay module in probe mode (bind address and port from the
   heartbeat config, bounded buffer, drop counter reported in the heartbeat).
   New Rust code; the agent today ships only local logs (`logs_linux.rs`,
   `logs_windows.rs`).
2. `POST /api/agent/logs` accepts `source_ip` per entry from probes (today it
   uses `request.client.host`, which for a remote agent is the customer's NAT
   address). Non-probe agents keep the current behaviour, but their entries are
   attributed to the agent's own host, not resolved via IP.
3. Host resolution (`_resolve_host_id`) becomes `(tenant_id, site_id, ip |
   hostname) → host_id`.
4. The direct listener keeps working: in single-tenant mode everything lands in
   the default tenant (today's behaviour). In multi-tenant mode it is **off by
   default**; an operator can bind a port to one tenant/site explicitly for
   on-prem cases. It never guesses.

This reverses a non-goal of the probe spec ("Probe-side syslog — syslog is
already push-based and works across networks"), which was written for one
tenant.

Per-tenant ingestion limits: `_global_rate_ok` drops messages installation-wide
once the global rate is hit, so one customer's flapping switch drops everyone's
logs. A per-tenant token bucket sits before the global one; drops are counted
per tenant and visible to that tenant.

### WebSocket and SSE fan-out

`services/websocket.py` broadcasts to every client, filtered only by role.
`WsClient` gains `tenant_ids` (the active tenant; for MSP portal views, the set
the user may see), resolved at `/ws/live` connect from the same
session/membership logic as HTTP (tenant passed as a query parameter and
validated). `broadcast()` gets a **required** `tenant_id` argument — no default,
so every call site (`broadcast_ping_update`, `broadcast_agent_metric`,
`_broadcast_ping_updates` in the scheduler, …) has to decide. The syslog live
tail (`services/syslog.py` `_subscribers`, `routers/syslog.py` `/stream`) gets
the same treatment: a subscriber queue is registered with its tenant, and
`_enqueue` only feeds matching queues.

### Notifications

`notify()` in `notifications.py` reads every channel from global settings. It
takes `tenant_id` and reads the tenant's channels; `notification_logs` rows get
the tenant; the cooldown key becomes `(tenant_id, title)`. `ee` adds
provider-level channels (an MSP NOC wants every customer's critical incidents in
one Teams channel), delivered in addition to the tenant's own.

### AI

All AI entry points (`services/ai_client.py`, `ai_context.py`, `postmortem.py`,
the daily summary in `scheduler.py`) go through one gate: `ai_enabled` for the
tenant must be true, else the feature is not offered and nothing is sent. The
context builders read only through the tenant-scoped session. `ai_usage_logs`
per tenant gives cost attribution and a monthly cap. The consent itself (who,
when, which provider) is an audit event. AI calls send hostnames and log lines
to a provider outside Switzerland; the opt-in UI says so.

## SaaS reachability and SSRF

Not isolation in the database sense, but a cross-tenant risk that only exists
once strangers share a backend: every check and integration that runs **in the
core** originates from the SaaS's own network. A tenant who adds a host
`10.0.0.5` or an integration at `http://169.254.169.254/` makes the SaaS probe
its own infrastructure.

In multi-tenant mode the core therefore only checks public addresses (resolved
address checked after DNS, like `_is_safe_url` does for webhooks, plus egress
firewalling at the container level). Anything private goes through a probe.
This makes probe-side integrations (Proxmox, UniFi, … — a non-goal of the probe
spec) a practical prerequisite for a useful SaaS offering; it should be its own
spec.

## Open Core boundary

### Principle

Core always has **exactly one tenant** (id 1, provider id 1), created by
migration. All isolation plumbing — `tenant_id` columns, context, filter, RLS,
ClickHouse policies, tenant-aware caches, WebSocket scoping — is core, because
it is a property of the schema and of every query, and two code paths would rot.
What `ee/` adds is the ability to have **more than one**, and everything that is
only valuable once there is more than one.

| Core (AGPL-3.0) | `ee/` (commercial) |
|---|---|
| tenant/provider/site tables, default tenant 1 | creating, suspending, deleting tenants (incl. PG + ClickHouse purge) |
| sites, probes, probe-based syslog | MSP portal: cross-tenant overview, incident board, tenant switcher |
| tenant context, filter, RLS, CH row policies | provider roles and provider-scoped API keys |
| memberships with the three roles (one tenant) | multiple memberships per user |
| local users + single LDAP | per-tenant/provider identity providers (OIDC, SAML), SCIM |
| notifications per tenant | provider-level notification channels |
| export of the one tenant | import into a multi-tenant installation |
| — | SLA reports, HA, AI premium (per owner decision) |
| — | plan quotas and per-tenant retention tiers |

### Code structure

```
backend/
  services/tenancy.py       # core: TenantContext, system_scope(), TenancyBackend protocol,
                            #       SingleTenancy (always tenant 1)
  ee/                       # commercial licence (ee/LICENSE), own package
    __init__.py             # register(app) -> adds routers, replaces TenancyBackend
    licensing.py            # signed licence verification
    tenancy.py              # MultiTenancy backend
    routers/                # tenants, providers, msp portal, idp, scim
frontend/src/
  ee/                       # MSP portal pages, tenant switcher
```

- Core never imports from `ee`. `main.py` does
  `try: import ee; ee.register(app) except ImportError: pass`, analogous to the
  auto-discovery in `backend/integrations/__init__.py`.
- Extension points are explicit protocols in core: `TenancyBackend`
  (`list_tenants_for(user)`, `can_create_tenant()`, `effective_role(user,
  tenant)`), `IdentityProviderBackend`, `NotificationFanout`. Core ships the
  trivial implementation of each.
- The frontend hides the tenant switcher when `GET /api/auth/me` reports one
  tenant; `ee` pages are lazy routes that only exist in an `ee` build.
- CI runs the full core test suite with `backend/ee/` and `frontend/src/ee/`
  deleted, so core cannot quietly depend on `ee`.

### Licence check

An offline-verifiable, Ed25519-signed licence file (customer, `max_tenants`,
feature list, expiry), verified with a public key embedded in the build — the
same primitive as agent update signing (`services/agent_signing.py`). Offline
because on-prem installs may be air-gapped. On expiry nothing is deleted and
monitoring keeps running; creating tenants and `ee` admin features are refused
and a banner shows. A monitoring system that stops monitoring because a licence
lapsed would be its own outage.

Being honest about the limit: tenancy enforcement lives in AGPL code, so a fork
can remove the "one tenant" check. That is inherent to this boundary. The value
of `ee` is the MSP portal, lifecycle, SSO and support, not the `if`.

## Migration

### Existing single-tenant databases (every install today)

One Alembic revision series (035 ff.), run by the update orchestrator after its
`pg_dump` backup:

1. Create `providers`, `tenants`, `sites`, memberships, `tenant_settings`,
   `user_preferences`; insert provider 1, tenant 1 (name from `site_name`),
   site 1 ("Default").
2. Add `tenant_id INT NULL` to all tenant-owned tables (list above), backfill
   `= 1`, then `SET NOT NULL` (no server default — new rows get their tenant
   from the context, never by accident). Tables are small (config
   and state); `snapshots` and `incident_events` are the largest and still fit
   a single `UPDATE` in an update window. Add `site_id = 1` where defined.
3. Rebuild unique indexes and composite FKs (`UNIQUE (tenant_id, id)` on
   parents first). `agents` is created by `create_all` in `migrate.py`, not by a
   migration — the revision must handle both "table exists" and the fresh-install
   path, like 034's `_has_column` guards.
4. `users.role` → `tenant_memberships(user, 1, role)`; the column stays one
   release for rollback, then goes.
5. Move tenant-scoped keys from `settings` to `tenant_settings` (tenant 1);
   `dashboard_layout` to `user_preferences` for every existing user.
6. Role split and RLS policies — last, in a separate revision, so a problem
   there can be rolled back without undoing the backfill. Requires the
   orchestrator to run migrations as `nodeglow_owner`; existing installs get the
   roles created by the migration using the current (owning) user.
7. ClickHouse step A via `_PHASE3_ALTERS`; step B as a background job.

Nothing changes for the user: one tenant, the same roles, the same settings.

### From "one instance per customer" into the SaaS

A per-tenant **export/import**, built for exactly this move and also usable for
offboarding (data portability) and backups of a single customer.

Export (core, any install — it exports its one tenant):

- `manifest.json`: schema revision, source installation id, tenant, row counts,
  checksums.
- Postgres: one JSONL file per table in FK order, all tenant-owned tables.
- ClickHouse: one Parquet file per table, `SELECT … WHERE tenant_id = …`,
  optional time window (syslog can be large; "last 30 days" is a valid choice).
- Secrets (`integration_configs.config_json`, `credentials.data_json`,
  encrypted settings) are decrypted with the source `SECRET_KEY` and the whole
  archive is encrypted with a passphrase-derived key. Plaintext secrets never
  hit disk.

Import (`ee`):

- Creates a new tenant and **remaps every id** (id map per table), including
  soft references: `snapshots.entity_id`, `alert_rules.source_id`,
  `backup_jobs.source_config_id`, `ping_hosts.parent_id`/`probe_id`,
  ClickHouse `host_id`, `agent_id`, `probe_id`, `syslog_messages.host_id`,
  and `bandwidth_metrics.source_id` (a string that may hold an agent or config
  id). `incidents.host_ids_hash` is recomputed from the remapped ids.
- Users are matched to existing global users by e-mail, else created and
  invited; memberships recreated.
- What cannot move: agent tokens and API keys are stored as
  `HMAC(SECRET_KEY, …)` and cannot be re-hashed without the raw secret.
  Sessions are not exported.

Agents are moved, not re-installed: before export, the source instance queues a
`pending_command = "migrate"` (the command channel already exists on the
heartbeat response) carrying the new `agent_server_url` and a one-time install
token for the target tenant/site. The agent re-enrols against the SaaS and
switches over; the source shows which agents have not yet moved. API keys are
listed in the import report for reissue.

Dry-run mode on import validates everything and writes nothing.

## Phased plan

Each step ships to main behind the fact that core still has one tenant; nothing
is user-visible until phase 7.

| # | Step | Where | Effort |
|---|---|---|---|
| 0 | Spikes: CH row policy with custom setting on 24.8 via clickhouse-connect; RLS + `set_config` per transaction with asyncpg/SQLAlchemy; column-based TTL | — | S |
| 1 ✅ | Schema foundation: new tables, `tenant_id` + backfill + composite uniques/FKs, `TenantScoped` mixin; static test "every table classified" | core | L |
| 2 ✅ | Tenant context: middleware resolution, `system_scope()`, session filter fail-closed; every router passes in single-tenant mode | core | L |
| 3 ✅ | Settings split + registry; `main.py` caches keyed; per-user dashboard layout | core | M |
| 4 ✅ | Auth binding: memberships replace `users.role`, API keys/install tokens/agents bound, audit fields, WebSocket/SSE scoping, notifications per tenant, in-process state table above | core | L |
| 5 | Scheduler: per-tenant analysis loops, fair-share queue for data-plane jobs, per-tenant metrics | core | M |
| 6 ✅ | ClickHouse: step A + helper filtering + row policies + two users; step B rebuild job | core | M + M |
| 7 ✅ | Postgres RLS + role split; CI job on Postgres asserting policies | core | M |
| 8 | Tenant-isolation test suite (built from phase 2 on, gating from phase 4) | core | M |
| 9 | `ee/` skeleton, licence verification, tenant lifecycle incl. purge, tenant switcher | ee | L |
| 10 | Probe syslog relay (agent) + `/api/agent/logs` per-entry source + site-scoped resolution | core (agent + backend) | M + S |
| 11 | MSP portal: cross-tenant overview, incident board, provider roles and keys, provider channels | ee | L |
| 12 | Export (core) / import (ee), agent `migrate` command | core + ee | L |
| 13 | SSO: OIDC, SAML, SCIM, home-realm discovery | ee | L |
| 14 | SaaS hardening: core egress restriction, per-tenant syslog/AI/CH quotas, plan limits | core + ee | M |

S ≈ days, M ≈ 1–2 weeks, L ≈ 3–5 weeks for one developer. Phases 1–8 (the
core foundation) are roughly three months; a first sellable MSP install (9–11)
another one and a half; SaaS readiness depends on probe-side integrations,
which are outside this plan.

## Implementation status

### Phase 1 — schema foundation (done)

Migrations `039`–`041` (the series starts at 039, not 035: 035–038 were
taken by other work in the meantime).

- **039** creates `providers`, `tenants`, `sites`, `tenant_memberships`,
  `provider_memberships` (schema only, `ee`) and `tenant_settings` (empty
  until phase 3); inserts provider 1, tenant 1 (name from `site_name`), site 1
  and advances the serial sequences; every existing user becomes a member of
  tenant 1 with `users.role`.
- **040** adds `tenant_id` to the 26 pre-existing tenant-owned tables,
  backfills 1, `NOT NULL` without server default (`audit_logs` stays
  nullable); `site_id` (backfilled 1) on `ping_hosts`, `agents`,
  `agent_install_tokens`, `subnet_scan_schedules`; `user_preferences` gets
  `tenant_id` and the primary key `(user_id, tenant_id)`.
- **041** adds `UNIQUE (tenant_id, id)` on the seven parents, turns the 10
  existing references between tenant-owned tables (plus the 4 new `site_id`
  references) into composite FKs, makes the global
  uniques per tenant and puts `tenant_id` first in the hot-path indexes of
  `snapshots` and `incidents`.
- Fresh installs (`create_all` in `migrate.py`) get the default rows from
  `models.tenant.ensure_default_tenant`, called by `init_db`.
- Classification: **29 tenant-owned, 9 global** tables
  (`models.tenant.GLOBAL_TABLES`, enforced by
  `tests/test_tenancy_classification.py`, which also checks the shape: index
  leading with `tenant_id`, composite references, per-tenant uniqueness).
- Verified on PostgreSQL 16 (`tests/test_migrations_tenancy_pg.py`, runs when
  `NODEGLOW_PG_TEST_URL` is set): upgrade from 038 with seeded data,
  cross-tenant FK rejection, `SET NULL (probe_id)`, downgrade to 038 and back,
  fresh install vs. chain with `alembic check`. The chain itself cannot run on
  SQLite (revision 022 uses a PostgreSQL regex) — that predates this work.

Deviations from the text above:

- `maintenance_windows` (not in the table) is tenant-owned; so are `sites`,
  `tenant_settings` and `user_preferences`.
- `user_preferences` keeps its column-per-preference shape (revision 038):
  primary key `(user_id, tenant_id)`, not `(user_id, tenant_id, key)`.
- `site_id` is **nullable**. The session filter stamps the tenant's default
  site (`TenancyBackend.default_site_id`) on new rows; NOT NULL can follow once
  site-scoped resolution (phase 10) sets it everywhere.
- The `SET NULL` references (`ping_hosts.probe_id`,
  `snmp_host_configs.credential_id`) are `ON DELETE SET NULL (column)` and
  therefore PostgreSQL-only DDL (`ddl_if`); SQLite does not enforce foreign
  keys in this code base anyway.
- Not added yet, because nothing reads them before phase 4:
  `sessions.active_tenant_id`, `users.is_operator`, `audit_logs.provider_id` /
  `acting_via`.
- Memberships are written once by 039. Users created afterwards (setup,
  users API, LDAP auto-provisioning) get no membership row; phase 4 must
  backfill again (`INSERT … WHERE NOT EXISTS`, as in 039) before switching
  auth over.
- `host_baselines` has no `site_id`; its unique key is
  `(tenant_id, host_key, hour, dow)`.

### Phase 2 — tenant context (done)

`services/tenancy.py`:

- `TenantContext` in a `ContextVar`; `tenant_scope(id)`, `system_scope(reason)`
  (reason mandatory), `current_tenant_id()`, `session_tenant_id(db)`,
  `bind_session(db, id)` (a session acting for one tenant when no context is
  set — used by the test fixtures).
- The filter is installed on SQLAlchemy's `Session` class (`models/__init__`),
  so it covers every session, including ones tests build themselves.
  `do_orm_execute` adds `with_loader_criteria(TenantScoped, tenant_id == ctx)`
  to ORM SELECT/UPDATE/DELETE (verified for joins, subqueries, `EXISTS`,
  aliases, unions, CTEs, relationship loads, bulk UPDATE/DELETE, `get()`).
  `before_flush` stamps `tenant_id` and the default `site_id`, refuses a
  different tenant and refuses moving a row between tenants.
- **Fail closed:** without a context, any ORM statement that touches a
  tenant-owned table raises `TenantContextError` (found by walking the
  statement, so `select_from`, joins and subqueries count), as does a flush
  of a new tenant-owned object. In `system_scope` reads are unfiltered and a
  new row must carry `tenant_id` explicitly (NULL only where the model allows
  it). Global tables need no context. Same behaviour in tests and production.
- **What is enforced in a tenant scope** (state after the phase-1/2 review,
  2026-10-11 — the first version covered less):
  - SELECT / UPDATE / DELETE: `with_loader_criteria`, as above.
  - ORM **bulk UPDATE by primary key** (`session.execute(update(M), rows)`)
    is not covered by the criteria → refused; code uses Core updates with an
    explicit `tenant_id` predicate (`log_intelligence._update_templates_by_id`).
  - An UPDATE that **sets `tenant_id`** (in `.values()` or the parameters)
    is refused outside system scope.
  - ORM **INSERT**: a row naming another tenant is refused, a missing
    `tenant_id` is filled from the context (single-row `.values()` and
    execute-time parameter rows; a multi-row `.values()` must name it);
    `insert().from_select()` is refused.
  - `select(M).from_statement(text(...))` on a tenant-owned model is
    refused unless marked `execution_options(nodeglow_tenant_filtered=True)`
    (the caller's SQL carries the predicate) — with or without a context.
  - Flush: new objects are stamped with the tenant **when added**
    (`transient_to_pending`), so a row created for A and flushed in B's
    scope is refused; changed or deleted objects must belong to the
    context's tenant (an object loaded in system scope cannot be changed
    from a tenant scope); an existing row's `tenant_id` never changes
    outside system scope, also when the old value was not loaded.
  - Not covered: `text()` and Core statements on `Model.__table__` (they
    must add the predicate themselves; RLS in phase 7).
- **Scheduled jobs start without a context**: APScheduler wakes its loop
  through the event loop, which carried the context of whoever last added
  or rescheduled a job (a request, with its tenant) — jobs then ran for that
  tenant only. `scheduler.ContextFreeExecutor` runs every job in an empty
  `contextvars.Context`; every job declares `per_tenant_job` or `system_job`
  (also the runtime-added phpIPAM sync).
- **Probe results** are stored only for hosts assigned to the reporting
  probe (in its tenant); others are dropped and logged.
- Extension point: `extensions.TenancyBackend` (protocol:
  `resolve_request_tenant`, `list_tenants_for`, `active_tenant_ids`,
  `effective_role`, `can_create_tenant`, `default_site_id`), registered with
  `registry.set_tenancy_backend()`; without a plugin
  `services.tenancy.SingleTenancy` (always tenant 1). This replaces the
  `ee.register(app)` sketch in "Code structure" — it follows the existing
  `ee_loader` / `extensions.registry` mechanism.

Wiring:

- **Middleware** (`inject_globals`): after the user, the backend resolves the
  tenant (`None` → 404), the role comes from `effective_role`, and the
  downstream app runs in `tenant_scope`; the context is reset afterwards.
- **Login/logout** (`/api/auth/`, skipped by the middleware): the audit row is
  written in the user's tenant (`user_tenant_scope`).
- **API keys** (`require_api_key`): key lookup in `system_scope`, then the
  request runs in the key's tenant.
- **Agents** (`/api/agent/*`, `/install/*`): token lookups (agent token,
  install token) in `system_scope`; the rest of report/logs/enrol runs in the
  agent's / token's tenant. Enrolment takes tenant and site from the install
  token (the legacy shared key enrols into tenant 1), and the re-enrolment
  hostname match and the PingHost match are now tenant-scoped.
- **WebSocket** `/ws/live`: tenant resolved like HTTP; broadcast scoping is
  phase 4.
- **Scheduler**: every job declares its tenancy (asserted by
  `test_every_scheduled_job_declares_its_tenancy`, and the job smoke test now
  hands jobs unbound sessions). `per_tenant_job` (one iteration per
  `active_tenant_ids()` in `tenant_scope`, one tenant's error does not stop the
  others) for correlation, log intelligence/analytics, alert rules, backup
  compliance, cleanup, weekly digest, self-check, disk predictions **and the
  data-plane jobs** (ping, integrations, SNMP, SSL, DNS, port discovery,
  MonitoringSource runners incl. subnet scans) and the `ee` daily AI summary.
  `system_job` for installation jobs: ClickHouse log cleanup, GeoIP update,
  legacy API-key cleanup (which writes one audit row per affected tenant).
  `check_disk_space` records the installation sample, then raises the
  incident per tenant.
- **Syslog**: the host cache (one listener for the installation) is built in
  `system_scope`; the template cache loads tenant 1's templates (the direct
  listener feeds the default tenant).
- **Startup**: `ensure_default_tenant` in `system_scope`.
- **Backup restore**: rows from a pre-040 backup get the importing tenant and
  its default site.

Deviation: the spec has data-plane jobs as one global pass in `system_scope`
with per-item tenant context. In phase 2 they run as per-tenant loops instead —
identical with one tenant, stamping and filtering for free, and no unscoped
insert path. Phase 5 replaces the loop with the global pass and the fair-share
queue. `system_scope` entries are logged at DEBUG, not yet audited (phase 4).

Performance: with a context the cost is one cached `with_loader_criteria`
option per statement (the tenant id is a bound parameter); the statement walk
runs only for context-less statements (in practice the global session/user/
settings lookups). Statement counts are unchanged
(`test_query_count_does_not_grow_with_hosts_or_incidents` passes). Rough
timing of `tests/test_routers/test_dashboard_v2.py` on SQLite: 1.18 s →
1.38 s for the module (best of 3, includes seeding the default tenant per
client); the heaviest single test 0.17 s → 0.18 s.

### Raw SQL and Core statements on tenant-owned tables (input for phase 7)

The ORM filter does not see these. Each must get an explicit tenant predicate,
become operator-only, or rely on RLS.

| Where | Statement | Tables | Today | Phase 7 / before tenant #2 |
|---|---|---|---|---|
| `routers/system.py` `system_status` | 11 × `text()`: counts and sizes | `ping_hosts`, `integration_configs`, `snapshots` (incl. `LATERAL` latest per config, oldest snapshot), `incidents` (4 counts), `alert_rules`, `log_templates`, `host_baselines`, `precursor_patterns`; plus `settings`, `pg_class`/`pg_namespace`, legacy `ping_results` | installation-wide counts | installation health → operator-only, or tenant predicate per count; RLS makes the counts per tenant |
| `services/backup.py` `export_backup` / `get_backup_info` | `SELECT * FROM <t>`, `SELECT COUNT(*)` over `EXPORT_TABLES` | the 17 tenant-owned tables of `EXPORT_TABLES` | whole database | operator-only in multi-tenant mode; tenants use the per-tenant export (phase 12) |
| `services/backup.py` `import_backup` | `TRUNCATE … CASCADE`, `INSERT INTO <t>`, `setval`, `session_replication_role` | same | replaces every tenant's data | operator-only; must not run as `nodeglow_app` (needs RLS bypass anyway) |
| `services/log_intelligence.py` `_write_template_batch` | Core `UPDATE log_templates` (`LogTemplate.__table__`, executemany) | `log_templates` | tenant predicate added explicitly | covered |
| `services/log_intelligence.py` `_write_template_batch`, `_upsert_baselines` | ORM-enabled `INSERT … ON CONFLICT` | `log_templates`, `host_baselines` | `tenant_id` put into the rows explicitly, conflict target includes it; refused without context | covered |
| `models/tenant.py` `ensure_default_tenant`, migration 039 | `setval` on serial sequences | `providers`, `tenants`, `sites` | owner/startup only | runs as owner |

The `text()` statements the spec listed in `services/digest.py`,
`services/postmortem.py`, `services/snmp.py` and `scheduler.py` no longer
exist (grep 2026-10-11). `ee/` contains no raw SQL. ClickHouse is unchanged
(phase 6).

### Phase 3 — settings split (done)

Migration `042`; code in `services/settings_registry.py`,
`models/settings.py`.

- **Registry**: every key is declared once (`SettingDef`: scope
  installation|tenant, type, default, `secret`, `fallback`, `prefix`).
  `get_setting` / `set_setting` route by it: installation keys in
  `settings`, tenant keys in `tenant_settings` of the current tenant — the
  ambient context, the session's binding, or an explicit `tenant_id=`
  (setup wizard, scheduler start, syslog host cache; it must agree with a
  context if one is set). A tenant key without a tenant raises
  `TenantContextError`; an **undeclared key raises `UnknownSettingError`**
  (stricter than the spec, which only asked for the tenant rule).
  `tests/test_settings_registry.py` walks the core and `ee` sources (AST:
  literals, constants, tuples, loop variables, small key helpers,
  f-strings) and fails on any key that does not resolve to a declaration;
  the two genuinely dynamic sites are listed with a reason.
- **042** copies the tenant keys (a list frozen in the revision, pinned to
  the registry by a test) with their `encrypted` flag to tenant 1 and
  deletes them from `settings`; `dashboard_layout` becomes the column
  `user_preferences.dashboard_layout` and is copied to every existing user.
  Downgrade copies tenant 1 back (and the first user's layout).
- **Classification** as in the table above, plus: `public_url`,
  `ai_provider` / `ai_anthropic_model` / `ai_openai_*` (the provider config
  that belongs with `claude_api_key`), the upgrade markers
  (`ai_optin_migrated`, `channel_secrets_encrypted_at`) and the ee licence
  keys are installation; `ai_redact_*`, `ai_restore_placeholders`,
  `ai_enabled_by/at`, `availability_target`, `agent_service_fail_reports`,
  `template_*retention_days`, `log_baseline_done_through` (state) and the
  Teams/Slack/ntfy channel keys are tenant. `smtp_*` fall back to an
  installation row when the tenant has none — except `smtp_to` (a tenant's
  recipients are its own).
- `main.py`: `_nav_cache` / `_settings_cache` are dicts keyed by tenant;
  `invalidate_*()` take a tenant (default: the current one, none: all).
- Startup upgrades follow the split: the AI opt-in upgrade switches AI on
  for the default tenant only (a pre-opt-in install has no other), the
  channel-secret encryption walks every tenant's rows.
- Backups export `tenant_memberships`, `tenant_settings`,
  `user_preferences`. Restoring a pre-042 backup replaces the tenant's
  settings with the backup's (`adopt_legacy_tenant_settings(replace=True)`);
  export timestamps (ISO strings) are parsed back on restore — asyncpg
  refused them, so restores on PostgreSQL failed;
  a pre-043 backup lets `users.role` win over memberships and decides
  `is_operator` (`memberships.sync_from_users_role`). The sequence reset of
  the restore now runs only for tables with an `id`, in a savepoint — on
  PostgreSQL the failing `setval` on `settings` aborted the transaction.
- Settings API shapes are unchanged. `site_name` also renames the tenant.

Deviations / not done:

- "tenant, floor at installation" (`ping_interval`, `proxmox_interval`)
  and "capped at installation/plan" (`*_retention_days`) are not modelled:
  there is one tenant and no plan. The global jobs run at the **fastest
  tenant's** interval and the digest cron at the **first tenant's**
  schedule (scheduler start); per-tenant cadence is phase 5.
- `claude_api_key` has no per-tenant override (ee).
- New users get the default dashboard layout; before the split a new user
  saw the installation's shared layout.

### Phase 4 — auth binding (done)

Migration `043`; code in `services/memberships.py`, `services/tenancy.py`,
`services/audit.py`, `services/websocket.py`, `notifications.py`.

- **Memberships are the source of truth.** `043` first re-syncs tenant 1's
  memberships from `users.role` (between 039 and 043 only that column was
  written, so 039's copies were stale where a role had changed), then
  backfills memberships for users created after 039; the setup wizard, the users API (and its form
  twins), LDAP login and backup restore write them. `users.role` mirrors
  the default tenant's role for one release (rollback; `043` downgrade
  refreshes it from the memberships) and is no longer read for
  authorisation anywhere: admin checks use `request.state.tenant_role`
  (`tenancy.request_role` / `request_is_admin`).
- **Resolution** (`TenancyBackend.resolve_access` → `TenantAccess(tenant,
  role, via)`, replacing `resolve_request_tenant` + sync `effective_role`):
  `X-Nodeglow-Tenant` header (WebSocket: `?tenant=`), else
  `sessions.active_tenant_id`, else the only membership. It must be a
  membership of an *active* tenant, otherwise 404 — also for a session
  whose active tenant the user is no longer a member of. Several
  memberships with no choice → 404 (no "last used" yet). Used by the
  middleware, `/ws/live`, `/api/auth/*` and the API-key session fallback.
- **Login** starts the session in the resolved tenant (stored in
  `active_tenant_id`); no membership → 403 and no session.
  `/api/auth/me` reports the membership role. `POST /api/auth/tenant`
  switches the session's tenant (membership-checked, CSRF-checked — the
  middleware skips `/api/auth/` — and audited in the target tenant). The
  core has one tenant; nothing in the UI uses it yet.
- **Users API** works on the request's tenant: it lists its members,
  creates users with a membership, changes the membership role, and
  "delete" revokes the membership (the user row goes when no membership is
  left). The last-admin rule counts the tenant's admins.
- **Operator**: `users.is_operator` (043: every admin; setup: the first
  user). Installation settings (LDAP, GeoIP, syslog port, agent URL,
  updates, app logs, the ee licence) require `is_operator()`.
  In the core the operator **is** the admin of the default tenant
  (`SingleTenancy.is_operator` = role in tenant 1 == admin), so a demoted
  admin loses the rights with the role; `users.is_operator` is kept equal
  to that by every role change (`memberships.grant`) for an ee backend,
  which decides by the flag.
- **API keys / tokens**: a key's tenant decides; a header naming another
  tenant → 404. API keys, agent tokens and install tokens of a tenant that
  is not active stop working. The shared enrolment key is refused once
  there is more than one tenant.
- **Audit**: `audit_logs.provider_id` (043 backfills it) and `acting_via`
  (`membership` | `provider_role` | `operator` from ee, plus `api_key`,
  `agent`, `system` — the spec's list did not cover keys and agent
  tokens). `log_action` takes the tenant from the context (NULL in system
  scope). **Writes in `system_scope` are audited** by the session filter:
  one `system_scope.write` row per affected tenant and scope (reason,
  tables); bulk statements are logged installation-level; reads are not
  audited (token lookups on every agent request would drown the log); the
  bootstrap of the default tenant opts out (`audit=False`).
- **WebSocket / SSE**: `WsClient.tenant_ids`; `broadcast()` has a required
  `tenant_id` (ping updates from the per-tenant job, agent metrics from
  the agent's tenant). Syslog live-tail subscribers register with their
  tenant; every parsed message carries its tenant (`syslog.TENANT_KEY`,
  stripped before the ClickHouse insert) and only reaches that tenant's
  queues; a message without one is dropped.
- **Notifications**: `notify(..., tenant_id=None)` takes the current
  tenant (raises without one), reads that tenant's channels, keys the
  cooldown by `(tenant, title)` and logs to `notification_logs` in the
  tenant. Provider-level channels (`NotificationFanout`) are ee.
- **In-process state keyed by tenant**: syslog host cache and allowlist,
  log-intelligence template cache / counts / new templates / bursts /
  baseline progress, disk-prediction cache, notification cooldown, `main.py`
  caches — and, found on the way, three more: the correlation streak
  counters (one tenant's end-of-cycle prune reset every other tenant's
  streaks), the correlation topology cache and `topology.cached_topology`
  (one tenant's topology served to the next within the TTL), and the
  `dashboard_v2` TTL cache key.
- **Fixed** (from phase 2): the syslog host-cache refresh and the startup
  template-cache load used `async with session, system_scope(...)`; the
  scope is a plain context manager, so both raised, were swallowed, and
  the host cache stayed empty since phase 2 (no host attribution, no
  allowlist).

Long-lived streams (`/ws/live`, the syslog live tail) re-run the
authentication and tenant resolution every `STREAM_REVALIDATE_SECONDS`
(30 s, `tenancy.revalidate_access`) and end when the session is gone, the
membership revoked, the role changed or the session switched tenant. The
per-tenant topology and dashboard TTL caches are bounded LRUs (512 / 2048
entries) instead of "clear when more than 8 / 32". The ee daily AI summary
hook reads every active tenant's hour in system scope (it read a tenant
key without a tenant and the job was never scheduled).

Not keyed, on purpose: the rDNS cache (what this installation's resolver
says about an address — no tenant data; the host match is per tenant), the
per-IP syslog rate limiter (listener-level; per-tenant buckets are phase
14), `rules._consecutive_matches` and the other caches keyed by global ids,
`ai_client._semaphore` (per-tenant fairness is phase 14).

Tests: `tests/test_routers/test_tenant_auth_binding.py` (two tenants, real
auth: membership role, sessions bound to a foreign tenant, header,
suspended tenant, switching, users API, API keys of A vs B, audit fields,
WebSocket and live-tail fan-out, notifications), `test_memberships.py`,
`tests/test_tenant_inprocess_state.py` (one test per keyed state),
`test_settings_registry.py`; the isolation smoke test now also covers
settings, notification history and API keys; the PostgreSQL migration test
covers 042/043 up and down.

Performance: one more query per authenticated request (the user's
memberships joined with `tenants`); unchanged statement counts otherwise.

### Phase 7 — PostgreSQL row-level security (done)

Revision `044`; code in `services/pg_rls.py`, `migrate.py`,
`alembic/env.py`, `models/base.py` (owner engine), `services/backup.py`.

- **Policies.** Every tenant-owned table (the 29 of the classification;
  `tenant_settings` and `user_preferences` included, `settings`, `users`,
  `sessions`, memberships, providers, tenants and the SNMP catalogue stay
  global) gets `ENABLE` + `FORCE ROW LEVEL SECURITY` and one policy
  `tenant_isolation`, `FOR ALL`, the same expression for `USING` and
  `WITH CHECK`:

  ```sql
  current_setting('nodeglow.system', true) = 'on'
  OR tenant_id = NULLIF(current_setting('nodeglow.tenant_id', true), '')::integer
  ```

  `audit_logs` rows with `tenant_id` NULL are visible in system scope only.
- **Settings per transaction.** A `before_cursor_execute` hook on the app's
  engine compares the tenant context (`tenancy.current()`, else the
  session's `bind_session` binding, carried to the connection by
  `after_begin`) with what the current transaction last got and, if it
  differs, runs `SELECT set_config('nodeglow.tenant_id', …, true),
  set_config('nodeglow.system', …, true)` on a separate cursor first. The
  state lives in the pooled connection's `info` and is reset at
  begin/commit/rollback, marked unknown at `ROLLBACK TO SAVEPOINT`, dropped
  at check-in. Cost: one round trip per transaction plus one per context
  change; nothing for SQLite.
- **Roles.** `nodeglow_app` (`NOLOGIN` until the app uses it,
  `NOSUPERUSER NOBYPASSRLS`, owns nothing) is created if missing, with the
  privileges of the connecting owner; it gets SELECT/INSERT/UPDATE/DELETE
  on all tables and USAGE/SELECT/UPDATE on all sequences (plus default
  privileges), not TRUNCATE, and only SELECT on `alembic_version`. When
  `DATABASE_URL` names its own user, that login is created or updated with
  the password from the URL — as a SCRAM verifier computed client-side, so
  the password never appears in the server log. Without CREATEROLE every
  role statement fails in a savepoint and is logged; the upgrade goes on.
- **Owner connection.** `DATABASE_OWNER_URL` (alias
  `MIGRATION_DATABASE_URL`, default `DATABASE_URL`) is used by `migrate.py`,
  `alembic/env.py` and the backup restore (TRUNCATE,
  `session_replication_role`). Owner connections start with
  `nodeglow.system=on` (asyncpg `server_settings`) so a data migration run by
  a non-superuser owner under FORCE still sees every row.
- **The switch.** `NODEGLOW_RLS` (`config.RLS_ENABLED`). `044` and
  `migrate.py` always create role, grants and policies; ENABLE + FORCE only
  with the flag on, and with the flag off they are switched off again. The
  app installs the hook only with the flag on. `migrate.py` re-applies the
  whole setup after every upgrade or stamp (a fresh install never runs
  044; later tables get their policy from the live classification; DDL only
  where the state differs — policies carry a version comment). A failure
  there is fatal only with the flag on.
- **Diagnostics.** At start the backend logs whether RLS protects its
  connection: error when RLS is on in the database but the flag is off
  (everything would look empty) or the tables are not all enabled; warning
  when the app user is a superuser or BYPASSRLS.
- **Raw SQL inventory.** `routers/system.py` counts run in the request's
  tenant and are now per tenant under RLS. The backup export/info/restore run
  in `system_scope` (installation-wide), the restore's raw part on the owner
  connection, and the three backup routes require the installation operator
  (`require_operator`; in the core the admin of tenant 1 — unchanged).
  Found on the way: on PostgreSQL the export read the dropped legacy table
  `ping_results`, which aborted the transaction, so every later table was
  exported empty, and a restore of such an export failed on its
  `TRUNCATE ping_results`; each table now reads in a savepoint and only
  existing tables are truncated.
- **Compose / install / updater.** The compose files build `DATABASE_URL`
  from `POSTGRES_APP_USER` / `POSTGRES_APP_PASSWORD` (falling back to
  `POSTGRES_USER` / `POSTGRES_PASSWORD` — an existing `.env` renders exactly
  the old URL) and `DATABASE_OWNER_URL` from `POSTGRES_USER`, and pass
  `NODEGLOW_RLS` (default `off`). `install.sh` writes
  `POSTGRES_APP_USER=nodeglow_app`, a random `POSTGRES_APP_PASSWORD` and
  `NODEGLOW_RLS=on` for new installs; its upgrade path keeps `.env`. The
  updater's migrate step runs `python migrate.py` (owner, plus the RLS
  setup) instead of bare `alembic upgrade head`, with `SKIP_MIGRATIONS=1`
  so the entrypoint does not run it twice, and with
  `NODEGLOW_RLS_DEFER_ENABLE=1`: the old backend keeps serving until the
  restart, maybe with the flag still off, so that step never switches RLS on
  (only keeps it on where it is); the new backend's entrypoint does.
- **Review fixes (2026-10-11).** The restore commits the request's session
  before using the owner connection (its open transaction from the API-key
  lookup held locks the owner's `TRUNCATE` waited on forever) and sets
  `lock_timeout = 30s` there. Compose builds the owner URL only for a
  two-user setup (`POSTGRES_APP_USER`), so an override of `DATABASE_URL`
  alone keeps migrating the app's database; mismatching URLs are logged as
  an error. `CREATE` on schema `public` is revoked from `nodeglow_app` and —
  when the owner owns the schema — from `PUBLIC` (PostgreSQL < 15).

Deviations:

- **No separate `nodeglow_owner` role.** The owner is the role that owns the
  schema today (the Compose stack's `POSTGRES_USER`, or whoever
  `DATABASE_OWNER_URL` names). Creating a `NOLOGIN nodeglow_owner` and
  moving ownership to it was rejected: it buys nothing RLS needs (the app
  role is neither owner nor BYPASSRLS either way), and every `pg_dump`
  would then carry `OWNER TO nodeglow_owner`, which fails to restore into a
  fresh cluster.
- **No `nodeglow_system` (BYPASSRLS) role.** System scope sets
  `nodeglow.system = on`, which the app role can set itself: RLS is the net
  for a forgotten predicate, not a boundary against SQL injection. A
  BYPASSRLS role would need a second connection pool for system scope —
  phase 14 material.
- **`NULLIF(…, '')` in the policy.** A custom setting reads `''` (not
  NULL) in a connection that set it in an earlier transaction, and
  `''::integer` raises.
- **Settings per statement, not once at `BEGIN`.** One transaction may
  change context (look a token up in system scope, continue in its
  tenant). `SAVEPOINT` statements never carry settings: SQLAlchemy emits
  them lazily with the first statement inside, possibly already in another
  context, and `ROLLBACK TO` would restore that.
- **RLS on without the role split** (CREATEROLE missing, or one user): the
  flag still enables and forces RLS; it protects a non-superuser owner
  connection, and the start logs that it cannot protect a superuser.
- **Downgrade of 044** drops the policies and switches RLS off but keeps the
  role and its grants, so an app already running as `nodeglow_app` keeps
  working.

Rollout (recommendation, documented in OPERATIONS.md "Switching an
existing install on"): new installs start with two users and RLS on.
Existing installs — production CT 116 included — upgrade with the flag off
and one user, i.e. no behaviour change, then switch deliberately in two
steps: (1) `POSTGRES_APP_USER` / `POSTGRES_APP_PASSWORD` with
`NODEGLOW_RLS=off` (app as `nodeglow_app`, no RLS) for a few days, (2)
`NODEGLOW_RLS=on`. Back out by setting the flag off (and removing the app
user) and restarting.

Tests: `tests/test_pg_rls.py` (SQLite suite: the hook with a fake
connection — contexts, transaction boundaries, savepoints, session binding,
pool check-in; URL parsing, SCRAM verifier, diagnostics; 044 covers the
classification). `tests/test_migrations_tenancy_pg.py` (PostgreSQL, CI
`migrations` job): fresh install and upgrade from 043 end with RLS enabled
and forced on every tenant-owned table; as `nodeglow_app` a raw select
without setting returns nothing, with a tenant only its rows, system scope
all; WITH CHECK refuses foreign-tenant rows; composite FKs still reject
cross-tenant references; the app role cannot TRUNCATE, disable RLS or write
`alembic_version`; flag off → RLS off again; downgrade removes the
policies; the single-user upgrade is unchanged; an owner without
CREATEROLE upgrades with a warning; the app's own hook (contexts in one
transaction, savepoint rollback, ORM insert, pool reuse); and six router
test modules (isolation smoke, auth binding, smoke, review fixes, API v1, dashboard) run against PostgreSQL
as `nodeglow_app` with RLS on (`tests/test_routers/conftest.py`, PostgreSQL
mode). Two test seeds needed an extra flush there: the unit of work does not
order inserts by foreign keys without a `relationship()` (a system-scope
audit row before its new tenant, a host before its new site) — irrelevant
in the core, but the ee tenant creation (phase 9) must flush the tenant
before writing rows of it in the same session.

### Phase 6 — ClickHouse (done)

Code in `services/clickhouse_client.py`, `services/clickhouse_rebuild.py`,
`clickhouse/init.sql`, `clickhouse/config.xml`. No Alembic revision
(ClickHouse schema changes live in the client's startup lists).

- **Step A** (`_PHASE3_ALTERS`, every start, idempotent):
  `tenant_id UInt32 DEFAULT 1` on `syslog_messages`, `ping_checks`,
  `agent_metrics`, `bandwidth_metrics`; `site_id UInt32 DEFAULT 0` on
  `syslog_messages`. Metadata-only: parts written before it read the
  column from the DEFAULT, i.e. every existing row reads as tenant 1 — that
  is the backfill, no mutation. The DEFAULT must therefore stay 1 as long as
  such parts exist (a changed default changes what old parts read as).
  Fresh installs (`init.sql`, `_PHASE2_SCHEMAS`) create `tenant_id` without
  a default (a forgotten stamp lands in tenant 0, which nobody reads) and
  leading the sort key. There are no other ClickHouse tables (ee has none;
  `syslog_aggregated` was dropped before).
- **Inserts** stamp `tenant_id` from each row and refuse rows without one
  (or with ≤ 0) before anything is sent: ping results from the host
  (`run_ping_checks` ICMP and agent-sourced, the manual check, probe results
  from the assigned host), agent metrics from the agent, bandwidth from the
  agent / integration config (`extract_*_bandwidth(tenant_id=…)`), syslog
  from the message's in-process tenant (`syslog.TENANT_KEY` →
  `tenant_id`; the direct listener is still `LISTENER_TENANT_ID`, agent
  logs the agent's tenant; a message without one is dropped and counted).
  `site_id` is written when a row carries it (0 otherwise, until phase 10).
- **Reads** (`query()`, `query_scalar()`, `query_chunked()` and every
  helper):
  - no context → `TenantContextError`, nothing is sent;
  - tenant scope → SQL that reads a tenant table must carry
    `tenant_id = {nodeglow_tenant:UInt32}` (`TENANT_FILTER`) once per
    `FROM`/`JOIN` of a tenant table, else `TenantContextError`; the
    parameter is bound from the context (a different explicit value is
    refused); `additional_table_filters` with `tenant_id = <tenant>` for
    all four tables goes with every query as a server-side net (a
    predicate in the wrong place cannot widen the read;
    `NODEGLOW_CLICKHOUSE_TABLE_FILTERS=0` switches the net off, not the
    predicate);
  - system scope → installation-wide on the main connection (readiness
    probe, ClickHouse log cleanup, the step-B job, the syslog-age part
    metadata in `/api/system/status`, operators only);
  - helpers start their WHERE with `tenant_clause()` (`1` in system scope);
    `_where_clauses` adds the predicate first.
  Every raw query in routers, services and ee got the predicate (every
  statement; dashboards, syslog router and API, incident detail,
  correlation, log intelligence incl. baselines / precursors / fleet
  patterns, alert rules, digest, self-check, ee AI context and daily
  summary). The self-check now judges freshness of the tenant's own
  streams; `/api/system/status` counts the request's tenant's rows.
  `tests/test_tenancy_clickhouse.py` scans every SQL string in core and ee
  statically for the predicate, in addition to the runtime guard.
- **Row policies + second user** (optional): with `CLICKHOUSE_TENANT_URL`
  (DSN of a restricted user, e.g. `nodeglow_app`) every tenant read runs as
  that user with the custom setting `SQL_nodeglow_tenant = <tenant>`. At
  startup the main connection creates/updates the user (password from the
  DSN), grants `SELECT` on exactly the four tables and `system.settings`
  (read by the client on connect), and creates per table
  `nodeglow_tenant_isolation … USING tenant_id =
  toUInt32(getSetting('SQL_nodeglow_tenant')) TO <user>` and
  `nodeglow_unrestricted … USING 1 TO ALL EXCEPT <user>` (the main user
  keeps reading everything regardless of
  `users_without_row_policies_can_read_rows`). A query without the setting
  errors (`getSetting` of an unset custom setting raises).
  `NODEGLOW_CLICKHOUSE_PROVISION_TENANT_USER=0` skips the provisioning for
  operators who manage ClickHouse access themselves. Unset (default, every
  existing install): one connection, the application filter only — nothing
  changes. `clickhouse/config.xml` sets
  `custom_settings_prefixes = SQL_`. clickhouse-connect 0.8.9 refuses
  settings it does not find in `system.settings`; the client registers the
  custom setting on the restricted connection (`allow_custom_setting`).
  Inserts, schema and system scope use the main (`CLICKHOUSE_URL`) user.
- **Step B** (`services/clickhouse_rebuild.py`, system job
  `run_clickhouse_tenant_rebuild`, hourly, first run 5 min after start,
  `NODEGLOW_CH_TENANT_REBUILD=off` disables it): see the module docstring.
  In short: preflight (plain MergeTree, Atomic database, step A done, free
  disk ≥ 2× what is left to copy, also per partition); work table
  `<t>__tenant_rebuild` from the table's own CREATE statement and engine
  clause with `tenant_id` prepended to `ORDER BY` (TTL, partitioning,
  settings, skipping indexes preserved; `tenant_id` without default);
  partition-wise copy, oldest first, `INSERT … SELECT … WHERE _partition_id
  = p AND _part IN (listed parts)` with a fixed `query_id` (an abandoned
  copy is killed first); per partition the highest copied block number and
  row count are persisted in `nodeglow_tenant_rebuild_progress` (the
  resume point; runs are bounded by `NODEGLOW_CH_REBUILD_BUDGET_SECONDS`,
  20 min). Rows inserted meanwhile are recognised by block number and
  appended; a partition changed by TTL / deletes / a merge across the
  boundary is copied again. Cutover: source TTL merges paused for the
  catch-up, then all merges stopped, final pass verified against the
  current part set, `EXCHANGE TABLES`, old data renamed
  `<t>__pre_tenant`. **Inserts never pause** (deviation from the spec's
  "pause the syslog flush"): rows that still land in the old table (an
  insert in flight, another backend instance) are in parts above the
  recorded block numbers — the old table's merges stay stopped — and are
  copied over in delta passes (0/2/10/35 s and again before the drop). A
  cutover that would need a full copy larger than
  `NODEGLOW_CH_REBUILD_CUTOVER_MAX_BYTES` (2 GiB) while merges are stopped
  is postponed (a monthly syslog partition is small again early in the
  month). The old table is dropped after `NODEGLOW_CH_REBUILD_GRACE_HOURS`
  (24); anything that does not reconcile after the exchange marks the
  table `attention` and keeps it. Startup restarts merges and TTL merges
  on the four live tables (a run that died in the cutover). State for
  operators in `/api/system/status` (`operational.clickhouse_tenant_rebuild`);
  `clickhouse_rebuild.complete()` is what ee must check before creating a
  second tenant.
- **Retention per tenant**: table TTLs stay the installation's ceiling; the
  nightly per-tenant `cleanup_old_results` deletes the tenant's
  `ping_checks` rows older than its `ping_retention_days`
  (`enforce_retention`: lightweight `DELETE` confined to `tenant_id`, only
  issued when a count finds rows; values ≥ the 30-day TTL are no-ops). This
  makes the setting effective for the first time (it was offered in the UI
  but had no effect).

First start of an existing install: the step-A ALTERs (milliseconds,
metadata only), the restricted user only if configured, and about five
minutes later the first rebuild run. At production size (~30 MB) the whole
step B completes in that run — four table copies, exchanges and delta
passes of well under a minute in total; the old tables stay 24 h and are
dropped by a later run. Disk needed meanwhile: about the size of the data
again. Tenant reads see the same rows throughout (tenant 1 = everything).

Tests: `tests/test_tenancy_clickhouse.py` (fail-closed for query() and
every helper, the predicate guard, binding, server-side settings,
row-policy routing, provisioning DDL, insert stamping per producer,
retention, static scan), `tests/test_clickhouse_rebuild.py` (copy
decisions, engine rewrite, preflight refusals, job behaviour),
`test_clickhouse_helpers.py` (each helper's SQL passes the guard and binds
the tenant). `tests/test_live_clickhouse.py` against ClickHouse 24.8 in CI
(now started like production: `config.xml`, `init.sql`, a user with access
management): step A on an old-schema table, isolated reads in two tenants
(helpers, raw SQL, `additional_table_filters` incl. subqueries), syslog
tenant/site, the row policy (setting required, only tenant tables
granted, main user unaffected), step B preserving per-partition and
per-tenant counts, resuming, carrying late rows, keeping TTL and indexes,
dropping after the grace period, refusing without disk. The live tests
were written without a local server; CI is their first run.

After review (2026-10-11):

- `syslog_messages` is created by the app as well (`init.sql` runs once and,
  with the image's entrypoint, not necessarily in the app's database).
- A failed `CREATE TABLE` or step-A `ALTER` fails the connect attempt (the
  client is published only with its schema), so it is retried instead of
  every insert failing until the next restart. The older optional ALTERs
  stay best effort.
- Restricted user: created with `DEFAULT DATABASE` (the main DSN's database
  if its own DSN names none, also passed on connect); a DSN naming the main
  user is refused; the connection is verified once (a read without the
  setting must fail on every tenant table, one with it must succeed) and
  tenant reads fail closed with `TenantIsolationError` while it does not
  (retried every minute) — a failed or partial provisioning is no longer
  just a log line. A renamed restricted user: the previous one (found in the
  policy's `apply_to_list`) loses its grants before the policies are
  re-targeted.
- Step B after the exchange: the old table's merge blockers are renewed on
  every run (a ClickHouse restart lifts them); rows at or below the recorded
  block may only shrink (TTL, deletes — the live table applies them too);
  only a part straddling the block is `attention`. Late rows go through a
  staging table (`<t>__tenant_delta`), an intent row and `MOVE PARTITION`,
  so a retry never duplicates them. Recovery from `attention`: check the
  live table, `DROP TABLE <t>__pre_tenant`.
- `ping_retention_days` is limited to 1–30 (the table TTL) in the settings;
  a stored larger value is logged once and expires at 30 days. CHANGELOG
  notes the behaviour change (production has 30: no extra deletion).

Deviations / not done:

- Two users, but not `nodeglow_app` + `nodeglow_system` as two new DSNs:
  the existing `CLICKHOUSE_URL` user is the unrestricted one (schema,
  inserts, system scope) and the restricted tenant user is optional
  (`CLICKHOUSE_TENANT_URL`), so existing installs keep working unchanged.
- Row policies are `FOR SELECT` only (ClickHouse has no `WITH CHECK`);
  inserts are confined by the per-row stamping, not by the server.
- Quotas / settings profile per tenant (`quota_key`, noisy neighbour) not
  done — phase 14.
- Per-tenant retention covers `ping_checks` only. `agent_metrics` and
  `bandwidth_metrics` (7-day TTL) and the syslog severity TTLs have no
  tenant setting; `proxmox_retention_days` still has no effect (its data
  are Proxmox snapshots in Postgres, governed by
  `integration_retention_days`) — remove or repurpose the key. A
  retention column + TTL expression (the spec's option) was not used: it
  would not apply to rows written before a change and interacts with
  `ttl_only_drop_parts = 1`.
- `site_id` exists on `syslog_messages` but nothing filters on it yet
  (phase 10).
- ee does not call `clickhouse_rebuild.complete()` yet — there is no tenant
  creation in ee (phase 9 must gate on it).
- The phase-8 isolation suite is to mark ClickHouse-backed leaks
  `xfail(strict=True, reason="phase 6")`. Its branch
  (`feat/tenancy-phase5-8`) had no such markers yet and was not merged
  when this was written: remove them when both branches meet (with
  `strict=True` they fail as XPASS once this phase is in).
  Merging the two branches conflicts in `scheduler.cleanup_old_results`
  only: keep both, reading `ping_retention_days` through phase 5's
  `tenant_jobs.retention_days(db, "ping_retention_days", 30)`.

### Open items for phases 5–8

- **Phase 5 (scheduler):** the daily AI summary fires at every tenant's
  hour and relies on its 20 h duplicate protection; per-tenant cadence (`ping_interval`,
  `proxmox_interval`, digest day/hour, daily AI summary hour) instead of
  "fastest / first tenant"; installation floors and retention caps; the
  fair-share queue and the global data-plane pass; a `tenant` label on job
  metrics; subnet-scan auto-added hosts still land in the tenant's default
  site, not the schedule's.
- **Phase 6 (ClickHouse):** done (see above). Left: ee's tenant creation
  must check `clickhouse_rebuild.complete()`; per-tenant retention beyond
  `ping_checks`; CH quotas per tenant (phase 14); `site_id` filtering
  (phase 10); the live tests' first run is CI.
- **Phase 7 (RLS):** done, see below. Still open from it: production runs
  with `NODEGLOW_RLS=off` until switched deliberately; a `BYPASSRLS` role for
  system scope (the bypass is a setting today); the `system_scope` write
  audit still does not see raw SQL (backup restore).
- **Phase 8 (isolation suite):** the route walker (every route with B's
  ids → 404, every response without B's canary, with session, API key and
  header) is still the smoke subset plus targeted tests; job isolation per
  analysis job.
- Still true from phase 2: work spawned with `asyncio.create_task` inherits
  the context; a task started in `system_scope` that writes must set its
  tenant itself (notifications and WebSocket broadcasts now take the
  tenant explicitly).
- WebSocket/SSE revocation is polled (≤ 30 s), not pushed; an ee tenant
  switcher or membership revoke could push a close via the hub.
- Later / ee: drop `users.role` after one release; manage `is_operator`
  (no API yet — set by 043 and the setup wizard only); the AI provider
  settings (installation keys) are still saved through the tenant admin's
  AI form — gate them on the operator when tenant admins are not
  operators; "last used tenant" at login; provider roles and operator
  access in `resolve_access` (`via = provider_role | operator`);
  `audit_logs.provider_id` on the `system_scope.write` rows is NULL.

## Risks

- **A missed path is a data breach, not a bug.** One unscoped query in a
  dashboard aggregate shows customer A's hosts to customer B. Mitigation: two
  enforcement layers, fail-closed context, and the enumerating test suite —
  same philosophy as `test_route_auth_coverage.py`.
- **RLS as a false green.** A missing context under RLS returns zero rows,
  which renders as "all quiet". Mitigation: layer 1 raises before layer 2 ever
  sees the query; RLS is never the only thing standing.
- **Performance regressions.** Every index gains a leading column; ClickHouse
  needs the rebuild for tenant-prefixed scans. Mitigation: `pg_stat_statements`
  (enabled in 032) before/after on the hot paths of 031.
- **Syslog rebuild on large installs** needs disk and time. Mitigation:
  resumable, partition-wise, preflight disk check, decoupled from correctness.
- **Scope creep into a rewrite.** The diff touches nearly every router and
  service. Mitigation: phases 1–4 are mechanical and keep single-tenant
  behaviour identical; the existing test suite must stay green at every step.
- **Open Core friction.** HA (the Redis leader lock in `scheduler.py`) and AI
  (`ai_client.py`, postmortems, daily summary) are already in the core tree.
  Moving them to `ee/` is still possible because no licence has been published
  (`README.md`: "Not decided yet"), but the window closes with the first public
  AGPL release.

## Test strategy

1. **Classification test** (static, SQLite): every table in `Base.metadata` is
   either `TenantScoped` with a `tenant_id` column or listed in
   `GLOBAL_TABLES = {"settings", "users", "sessions", "snmp_mibs", "snmp_oids",
   "providers", "tenants", …}` with a justification — a new table cannot be
   forgotten, exactly like `PUBLIC_ROUTES`.
2. **Route isolation test** (`tests/test_routers/test_tenant_isolation.py`):
   seed tenants A and B, one object of every type in each, every B object
   carrying a canary string (`CANARY-B-<table>`). Log in as a user of A. Walk
   `app.routes` like `test_route_auth_coverage.py`:
   - routes with path parameters, called with **B's** ids → 404, and B's row
     unchanged after mutating methods;
   - every list/detail/aggregate response for A → body contains no `CANARY-B`;
   - the same with an API key of A, and with `X-Nodeglow-Tenant: B` (→ 404);
   - an allowlist only for installation routes (`/health`, `/metrics`, operator
     endpoints), justified per entry.
3. **Job isolation tests**: run each analysis job with data in A and B; assert
   incidents, notifications, templates, baselines and AI context stay in their
   tenant. One test per row of the in-process-state table (same alert title in
   A and B both notify; Proxmox clusters named `pve` in A and B both collect;
   same template in A and B keep separate counts and examples).
4. **Fail-closed tests**: tenant-owned query without context raises; ClickHouse
   helper without context raises; `get_setting` of a tenant key without context
   raises.
5. **Postgres CI job** (extends the existing `migrations` job in
   `.github/workflows/ci.yml`): fresh install and upgrade-from-034 both end with
   RLS enabled and forced on every tenant-owned table; a raw `text()` select as
   `nodeglow_app` without GUC returns zero rows; composite FKs reject a
   cross-tenant `probe_id`.
6. **ClickHouse integration test**: row policy rejects a query without the
   setting; tenant A's setting never returns B's rows; the step-B rebuild
   preserves row counts per partition.
7. **WebSocket/SSE tests**: a client of A receives no event triggered in B.
8. **Core-without-ee job**: delete `ee/`, run everything.
9. **Export/import round trip**: export tenant, import into a fresh
   installation, compare counts and spot-check remapped references.

## Open questions for the owner

1. **Provider level in SaaS**: will MSPs resell through the Swiss SaaS (needs
   the provider level as designed), or is the SaaS only for direct customers
   (provider level could stay invisible)?
2. **AI and HA in `ee`**: move the existing AI features and the Redis leader
   election out of core before the first AGPL release, or keep a basic version
   in core and put only "premium" in `ee`? Where exactly is the line for AI?
3. **AI provider in SaaS**: one operator-owned Anthropic key for all opted-in
   customers (billed through), or bring-your-own-key per tenant, or both?
4. **Direct syslog in SaaS**: is probe-only ingestion acceptable for SaaS
   customers, or must there be a probe-less option (TLS syslog with client
   certificates per tenant)?
5. **Probe-side integrations**: the SaaS is of limited use without them (see
   "SaaS reachability"). Prioritise that spec ahead of or alongside phase 9?
6. **Retention**: free per-tenant values or a fixed set of plan tiers?
7. **Tenant deletion**: immediate purge, or a grace period (e.g. 30 days
   suspended) before PG and ClickHouse data is destroyed? Swiss nDSG and the
   contracts will drive this.
8. **Per-tenant encryption keys** (envelope encryption, crypto-shred on
   deletion): needed for the SaaS contract, or is one `SECRET_KEY` plus
   database encryption at rest sufficient?
9. **Operator access to tenant data** in SaaS: should it require the
   customer's explicit time-limited approval ("support access"), or is an
   audited membership enough?
10. **Licence enforcement on expiry**: is "keep monitoring, block admin
    features" the right behaviour for on-prem `ee`?
