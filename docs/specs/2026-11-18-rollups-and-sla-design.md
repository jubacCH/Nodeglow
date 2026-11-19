# Metric rollups, availability history and SLA reports

Status: design · 2026-11-18 · approach chosen by the owner (A: record state changes) · no code yet.

## Why

Nodeglow is meant to be sold to SMBs that run it for their own infrastructure and to MSPs that
run it for their customers. Both ask the same question at the end of a month: *did we hit the
availability target, and where did the downtime come from?* For the MSP the answer is evidence
under a contract. The end customer can dispute it ("why does Tuesday not count?"), so the figure
must be explainable minute by minute and must not change after it was sent.

Today Nodeglow cannot answer that:

- **Storage is too short.** `ping_checks` keeps 30 days, `agent_metrics` and `bandwidth_metrics`
  7 days, fixed by table TTL (`services/clickhouse_client.py`, `_PHASE2_SCHEMAS`). There are no
  rollups. The retention plans in `ee/backend/nodeglow_ee/tenancy/plans.py` offer 90 and 365
  days, but the module itself says "a tier is a ceiling, never a promise beyond those", so the
  larger tiers promise more than is stored.
- **Availability is counted in checks, not in time.** `services/dashboard_v2.section_availability`
  computes successful checks / all checks over 30 days. Maintenance and no-data periods produce
  no rows and drop out silently. A host that was not observed for two days (dead probe) looks
  *better*, not worse, and there is no way to show afterwards which minutes were excluded and why.
- **State exists only as "now".** `services/host_state.py` already knows exactly the states an
  SLA needs (down, warning, degraded, unknown, maintenance, up, disabled), but it computes them
  for the current moment only. Nothing records them over time.
- There is no notion of a *service* ("Mail", "ERP") made of several hosts, and no business hours.

## Goals

1. Keep metrics for months at reduced resolution, so charts over a quarter or a year work and the
   retention plans become true.
2. Record the state of every host and every service over time, minute-accurate, using the same
   rules as the UI (`host_state.evaluate`).
3. Compute availability per host and per service for any period, with every minute accounted for
   as available, impaired, down, maintenance, unknown or excluded, shown separately.
4. Services: named sets of hosts with a rule for when the service is down ("all must run" or
   "at least N of M"), their own target and optional business hours.
5. Monthly figures per host and service that are frozen after the month closes and only change
   through an explicit, audited recompute.
6. Core: history, services, availability views and CSV. `ee/`: per-customer PDF report, branding,
   scheduled delivery (license decision of 2026-10-10: "SLA-Reports pro Kunde" belongs in `ee/`).

## Non-goals (this spec)

- Error-budget alerts ("80 % of this month's budget used"). Easy on top of goal 3, later.
- Members other than hosts (integrations, single HTTP checks). The member table is generic so
  they can be added without a migration of existing rows.
- Automatic public-holiday calendars (cantonal). Holidays are a manual date list per calendar.
- A public status page. It would read the same history, but is its own feature.
- Rolling up syslog. Syslog keeps its severity TTLs.

## Anti-goals

- **A figure that looks better because data is missing.** Unknown time is never counted as
  available. It is either excluded *and shown*, or counted as down, per service setting.
- **A silent recorder.** If the recorder stops, the gap must appear as `unknown` with a reason, and
  `self_check` must raise an incident. This is the ping-insert failure of 2026 (4.5 months of
  "success" with an empty table) in another place; it must not happen here.
- **A sent report that changes.** Raw data expiring, a maintenance window edited later or a code
  change must not alter a frozen month.
- **Two truths.** The dashboard's availability card and the report must use the same computation.

## Constraints

- 50 to 500 hosts per tenant; several tenants per installation (SaaS and MSP). Every new table
  is tenant-scoped (`TenantScoped`, RLS, ClickHouse tables lead their sort key with `tenant_id`).
- ClickHouse schema has no versioned migrations; tables are created idempotently on first connect
  (`_PHASE2_SCHEMAS`, `_PHASE3_ALTERS`). New ClickHouse objects follow that pattern.
- Postgres migrations run automatically on container start (`entrypoint.sh`), CI checks
  `alembic check`. Alembic head on `main` is 054; `feat/agent-reinstall` (not merged) uses 055.
  Take the next free number when implementing.
- Jobs: one leader-elected APScheduler, per-tenant loops via `services/tenant_jobs.per_tenant_job`,
  every job wrapped in `instrument_job` so `self_check` sees it.
- Releases must stay roll-back safe: a release only adds schema, removals come one release later.

## Approaches considered

**Part 1, metric rollups.** ClickHouse materialized views into aggregate tables is the only
sensible way: it runs inside the insert, needs no job that can fail silently, and ClickHouse is
built for it. A Python job that aggregates periodically was rejected for exactly that reason.

**Part 2, where the SLA history comes from.**

- **A — record state changes (chosen).** A job evaluates `host_state` every minute for all hosts
  and services and stores only the changes as periods. SLA is interval arithmetic.
  For: same truth as the UI; maintenance, unknown (dead probe) and degraded are exact because
  `host_state` already knows them; minute-accurate, so business hours and DST are exact; little
  data. Against: history starts at deploy (an approximate backfill from 30 days of `ping_checks`
  is possible); the recorder is a new moving part whose gaps must be made visible.
- **B — reconstruct from ping rollups.** 5-minute buckets of ok/total plus maintenance windows
  from Postgres. For: 30 days of past immediately, no new job. Against: a manually set
  maintenance flag, a dead probe and degraded cannot be reconstructed afterwards, and those are
  the minutes an MSP customer disputes; only 5-minute accurate.

The owner chose A on 2026-11-18. Part 1 is independent of that choice.

## Design

### Part 1 — metric rollups (ClickHouse)

Three levels per raw table, each filled by its own materialized view reading the raw inserts
(not chained, so one broken level cannot starve the next):

| Raw table | 5 min | 1 h | 1 day |
|---|---|---|---|
| `ping_checks` | `ping_checks_5m` | `ping_checks_1h` | `ping_checks_1d` |
| `agent_metrics` | `agent_metrics_5m` | `agent_metrics_1h` | `agent_metrics_1d` |
| `bandwidth_metrics` | `bandwidth_metrics_5m` | `bandwidth_metrics_1h` | `bandwidth_metrics_1d` |

- Engine `AggregatingMergeTree`, `ORDER BY (tenant_id, <entity key>, bucket)`, partition by month
  (5 min, 1 h) or year (1 day).
- Columns per numeric metric: count, sum (for the mean), min, max and a p95 state
  (`quantileTDigestState(0.95)`). Ping additionally `countIf(success)`, so check-based success
  rate stays available per bucket. Bandwidth: sum and max of the rates, last of the counters.
  `agent_metrics.data_json` is not rolled up.
- TTL (installation ceiling): 5 min **90 days**, 1 h **400 days**, 1 day **5 years**. Raw TTLs stay
  as they are.
- No per-tenant retention for rollups: the table TTLs above apply to every tenant, plans do not
  cap them (owner decision, see "Retention and plans"). Rollup tables are not added to
  `RETENTION_RULES` or `RETENTION_KEYS`. A tenant purge still deletes its rollup rows.
- **Order with the tenant rebuild:** `services/clickhouse_rebuild.py` may still be rebuilding a raw
  table (staging plus `EXCHANGE TABLES`). The views and the backfill for a table are created only
  once the rebuild reports that table done; until then the table has no rollups and charts fall
  back to raw data. *(builder's call, confirmed in review)*
- **Backfill:** once per table, `INSERT INTO <rollup> SELECT … FROM <raw>` for the data that already
  exists (30 days ping, 7 days agent and bandwidth), guarded so it runs exactly once and does not
  double-count rows the view already inserted (backfill only up to the view's creation time).
- **Readers:** chart endpoints pick the resolution from the requested range: up to 48 h raw, up to
  30 days 5 min, up to 400 days 1 h, beyond that 1 day. One helper in `clickhouse_client` decides,
  so every chart behaves the same.

### Part 2 — availability history (Postgres)

**Table `availability_periods`** (tenant-scoped):
`id, tenant_id, subject_type ('host'|'service'), subject_id, state, impaired (bool), reason,
started_at, ended_at (NULL = open), source ('recorder'|'gap'|'backfill')`.

- A partial unique index allows exactly one open period per subject (protects against two
  recorders writing at once).
- Postgres, not ClickHouse: the data is small (one row per state change), relational (joins with
  services, incidents, exclusions), needs RLS like the rest of the configuration, and needs
  updates (closing a period).

**Recorder job `availability_recorder`**, every 60 s, `fleet_job` (tenants run concurrently like
the other data-plane jobs; `per_tenant_job` runs them one after another and would skip runs with
several large tenants, producing its own gaps), `instrument_job`:

1. Load **all** hosts of the tenant, including disabled ones (the dashboard's `load_base` passes
   only active hosts, so its cache is not reused), and call `host_state.host_states` directly.
2. Map to the SLA state (table below), derive every service's state from its members.
3. For each subject compare with its open period. On a change, the new period starts at the
   time the change was *observed*, not at the run: `observed_at` of the first check with the new
   result (first failed check for down, first successful check for recovery), never earlier than
   the start of the period it closes. Changes without a check time (maintenance window begins,
   probe turns stale) start at the run time. This removes the up-to-60-s offset per change, which
   is exactly what an MSP customer would dispute.
4. Changes are written in one transaction per tenant. Store `last_recorded_at` per tenant.

**Gaps:** when a run finds `now - last_recorded_at > 3 × interval`, it closes every open period at
`last_recorded_at`, inserts an `unknown` period with `source='gap'` and reason "recorder not
running" for the gap, then continues normally. `self_check` must raise an incident when the
recorder stops (active as soon as the tenant has at least one host). Its freshness sources today
only ask ClickHouse for `max(timestamp)`; a Postgres-backed source reading `last_recorded_at` is a
small new mechanism in `self_check`, not just another entry. The job check via `instrument_job`
covers the case where the job runs but fails.

**State mapping:**

| `host_state` | SLA state | Counts as |
|---|---|---|
| up | available | available |
| degraded, warning | available, `impaired = true` | available, shown as "impaired" minutes |
| down | down | downtime |
| maintenance | maintenance | excluded, shown |
| unknown | unknown | excluded and shown, or downtime (`unknown_policy`: per service; for hosts the tenant setting `host_unknown_policy`, default `exclude`) |
| disabled | disabled | excluded, shown |

Down counts from the first failed check, without the notification grace. The grace is about
noise in alerts, not about whether the host was reachable.

**Backfill:** one-time, per tenant, from the 30 days of `ping_checks` before the recorder's first
run: success → available, failure → down, no row for longer than the host's window → unknown
(core window for core hosts, `probes.result_window` for probe hosts, as in `host_state`),
current maintenance window definitions applied. A manual maintenance flag (`maintenance_until`)
has no history and cannot be reconstructed; such minutes appear as down or unknown. Periods get
`source='backfill'`. A month that contains backfill periods is labelled "partly reconstructed" in
every view and report, with that limitation stated.

### Services

Tables (tenant-scoped):

- `services`: `id, tenant_id, name, description, rule ('all'|'min'), min_available (int, for
  'min'), target_pct (numeric, e.g. 99.5), business_hours_id (nullable), unknown_policy
  ('exclude'|'down', default 'exclude'), created_at, updated_at`.
- `service_members`: `service_id, member_type ('host'), member_id`. Only `host` in this spec.

Service state, derived each recorder run from member SLA states (`A` = available incl. impaired,
`D` = down, `M` = maintenance, `U` = unknown, disabled members are ignored):

- **rule `all`:** any `D` → down; else any `M` → maintenance; else any `U` → unknown; else
  available (impaired if any member is impaired).
- **rule `min` (N of M):** `A ≥ N` → available (impaired if fewer than all non-disabled members
  are available, or any available member is impaired); else `A + M ≥ N` → maintenance; else
  `A + M + U ≥ N` → unknown; else down.

Hosts have no own target and run 24/7; their availability is reported against the tenant-wide
`availability_target` setting that exists today.

### Business hours

Table `business_hours` (tenant-scoped): `id, name, timezone, schedule (JSON list of
{weekday, start, end}), holidays (JSON list of dates)`. Optional per service. Without it the
service runs 24/7.

Computation: for the requested period, build the business-hour intervals day by day in the
calendar's timezone (`zoneinfo`) and convert each to UTC, so DST days are exact (e.g.
Europe/Zurich 2026-03-29 has 23 h, 2026-10-25 has 25 h). Availability is computed only over the
intersection of the period with those intervals. Outages outside business hours are listed in
the report as "outside service hours", not counted.

### Computing availability

One pure function, used by the dashboard, the API and the report:

```
availability(periods, window, business_hours=None, exclusions=(), unknown_policy='exclude')
  -> {available_s, impaired_s, down_s, maintenance_s, unknown_s, excluded_s, disabled_s,
      in_scope_s, pct, coverage_pct, outages: [...]}
```

- `pct = available / (available + down)` with `unknown` added to `down` when
  `unknown_policy = 'down'`.
- `coverage_pct = (available + down) / in_scope` shows how much of the period was actually
  measured. Below 95 % every view flags the figure as "low coverage".
- `outages` lists every down period in scope with start, end, duration and the incident that
  covered it, if any (match on host and overlap with `incidents`).

`dashboard_v2.section_availability` is switched to this function (rolling 30 days), so the
dashboard and the report agree. The fleet figure **sums the seconds of all hosts** before
dividing (fleet-equivalent, like today's check count, so `budget_minutes` keeps its meaning),
not an average of host percentages. Its `method` text changes accordingly.

### Manual exclusions

Table `sla_exclusions` (tenant-scoped): `id, subject_type, subject_id, started_at, ended_at,
reason, created_by, created_at`. For the agreed emergency maintenance that nobody entered in
time. Admin only, audited. Excluded time is counted as `excluded`, shown with its reason. Allowed
for open months; for a frozen month only through a recompute.

### Monthly reports and freezing

Table `sla_reports` (tenant-scoped): `id, subject_type, subject_id, month ('YYYY-MM'), version,
timezone, target_pct, the figures of the availability function, met (bool), outages (JSON),
partly_reconstructed (bool), computed_at, frozen_at, superseded_by (nullable)`.

- Months follow the tenant's `timezone` setting (exists, default UTC).
- A daily job `sla_freeze` computes and freezes month M on day 3 of M+1 at 00:00 tenant time
  (two days of grace for exclusions entered late). Idempotent: an existing frozen row for that
  subject and month is never rewritten.
- **Recompute** of a frozen month: admin only, with a reason, writes a new version, sets
  `superseded_by` on the old row, writes an audit entry. The old version stays readable.
- Frozen reports are kept independent of any retention setting (they are small and they are the
  evidence). `availability_periods` are kept for an installation-wide
  `availability_retention_days` (default 1825, the same as the daily rollups), not a tenant
  setting and not plan-capped; deleting old periods never touches frozen reports.
- A recompute is refused when the month's periods are no longer complete (retention reached),
  because it would turn every minute into `unknown`. The frozen version stays.

### Retention and plans

`plans.py` has one `retention_days` per tier (30/90/365) and `retention_cap` applies it to every
key in `RETENTION_KEYS`. **Owner decision 2026-11-18: history is not capped by plans.** Plans keep
capping detail data (raw tables and the existing `RETENTION_KEYS`) as today. Hourly and daily
rollups, the 5-minute rollups and `availability_periods` are outside the plan cap and live for
the installation ceilings for every tenant. Year-over-year views and recomputes therefore work
on every plan. The `plans.py` docstring is updated to say which data a tier caps and that the
history is not part of it.

### API (core)

- `GET /api/v1/availability?subject=host:12|service:3&from=&to=` → figures plus periods.
- `GET|POST|PATCH|DELETE /api/v1/services` and members.
- `GET|POST|PATCH|DELETE /api/v1/business-hours`.
- `GET|POST|DELETE /api/v1/sla-exclusions`.
- `GET /api/v1/sla-reports?month=` (list), `GET /api/v1/sla-reports/{id}`,
  `GET /api/v1/sla-reports.csv?month=`, `POST /api/v1/sla-reports/{id}/recompute` (admin).

Write endpoints are admin or editor as with hosts; exclusions and recompute are admin only.
Every write is audited.

### UI (core)

- **Services** page: list with current state, month to date vs target, coverage. Detail: month
  timeline bar (available / impaired / down / maintenance / unknown / excluded), outage list with
  incident links, members, rule, business hours.
- **Host detail:** an "Availability" section with the same timeline and figures.
- **Reports** page: month picker, table of hosts and services with figure, target, met, coverage,
  "partly reconstructed" badge, CSV export. Frozen months show version and freeze date.
- **Business hours** under Settings.
- Charts over long ranges use the rollup resolution (Part 1) without UI changes beyond the range
  picker offering 90 days and 1 year.

### `ee/`

- PDF report per customer (tenant) and month, with the MSP's branding (logo, colours, footer).
- Scheduled delivery by e-mail after freezing.
- MSP view across tenants: one table of all customers' services for a month.

## Rollout (one PR each)

1. Metric rollups with backfill and resolution helper (independent of the rest).
2. `availability_periods`, recorder, gap handling, self-check source, backfill, availability
   function, dashboard switched to it, host detail section.
3. Services and business hours (tables, API, UI, service state in the recorder).
4. Exclusions, monthly reports, freezing, recompute, Reports page, CSV.
5. `ee/`: PDF, branding, delivery, MSP view.

## Tests that must exist

- The availability function against hand-computed cases, including both DST days of 2026 in
  Europe/Zurich, business hours across midnight, exclusions overlapping outages, both
  `unknown_policy` values.
- The service-state rules as a truth table for `all` and `min`.
- Recorder: a state change opens and closes periods; a simulated gap produces an `unknown` period
  with `source='gap'`; two concurrent runs leave one open period per subject.
- The recorder writes real rows (assert row counts in the database, not only "did not raise";
  see the ping-insert failure).
- Freeze is idempotent; recompute creates a new version and an audit entry.
- Tenant isolation for every new table and endpoint.
- Rollups: the view's aggregate equals an aggregate over the raw rows for the same buckets.

## Owner decisions

1. **How plans cap the new retention** (2026-11-18): not at all. Plans cap detail data only;
   rollups and availability history follow the installation ceilings for every tenant.

## Calls made while writing (check these)

- Recorder as `fleet_job`, not `per_tenant_job`.
- Periods start at the observed check time, not at the recorder run.
- Hosts: `host_unknown_policy` as a tenant setting, default `exclude`.
- Dashboard fleet figure sums seconds across hosts.
- Rollups per table only after the tenant rebuild of that table is done.
- Recompute refused once periods have expired.
- Frozen reports are never deleted by retention.
- Freeze on day 3 of the following month; holidays as a manual list; hosts run 24/7.

## Left to the builder

- Exact column types and names of the rollup tables, and how the p95 state is merged in queries.
- How the backfill guard is stored (a settings key or a small marker table).
- Whether the recorder reuses the dashboard's host-state cache or calls `host_states` directly.
- Layout of the timeline bar within the existing design tokens.
