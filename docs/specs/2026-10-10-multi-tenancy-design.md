# Multi-tenancy

Status: design · 2026-10-10

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
| 1 | Schema foundation: new tables, `tenant_id` + backfill + composite uniques/FKs, `TenantScoped` mixin; static test "every table classified" | core | L |
| 2 | Tenant context: middleware resolution, `system_scope()`, session filter fail-closed; every router passes in single-tenant mode | core | L |
| 3 | Settings split + registry; `main.py` caches keyed; per-user dashboard layout | core | M |
| 4 | Auth binding: memberships replace `users.role`, API keys/install tokens/agents bound, audit fields, WebSocket/SSE scoping, notifications per tenant, in-process state table above | core | L |
| 5 | Scheduler: per-tenant analysis loops, fair-share queue for data-plane jobs, per-tenant metrics | core | M |
| 6 | ClickHouse: step A + helper filtering + row policies + two users; step B rebuild job | core | M + M |
| 7 | Postgres RLS + role split; CI job on Postgres asserting policies | core | M |
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
