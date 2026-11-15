# Concept F, phase 3: evidence on incidents, a chart per incident, sites

Status: design · 2026-11-15 · owner decisions open (see "Owner decisions") · no code yet.
Follows the overview of design concept F
([`docs/design/directions/concept-f.md`](../design/directions/concept-f.md)): phase 1 (shapes
and rail, #60) and phase 2 (overview from existing data, #61) need no backend change. This
document covers the three things the new overview shows but the backend cannot deliver today.

## Why

The concept F overview promises three things that the dashboard API does not have:

1. **Evidence on an incident, with an honest label.** Every relation an incident claims should
   say *how* it is known: **Confirmed** (structural: topology parent, same probe, same
   integration), **Rule-based** (a correlation rule fired) or **Suspected** (only overlap in
   time or a deviation from a baseline). `docs/design/05-dashboard-api.md` lists
   "Beziehungslabels am Incident" under "not done". Phase 2 derives "Confirmed" in the browser
   from `topology.parents` and shows the rule as "Rule-based", but:
   - the browser sees only the 12 parents the dashboard sends, so a cause further away is missed;
   - the rules already know the cause (`upstream_failure` computes the failed parents, see
     `services/correlation.py:_rule_host_down`) and throw it away after building the title;
   - "Suspected" cannot be derived at all in the browser.
2. **A metric chart for the incident the user opened.** `section_latency` covers only the most
   severe open incident. Any other incident shows no chart.
3. **Hosts by site.** The overview groups hosts by checker (Direct or probe) because there is no
   site in the read model. A site table exists (`models/tenant.py: Site`), `ping_hosts`,
   `agents` (and so probes, which are agents with `is_probe`), `agent_install_tokens` and
   `subnet_scan_schedules` carry `site_id`, enrolment picks `default_site_id`. There is no API, no
   UI and no read model. This is backlog item B-14 in `02-information-architecture.md`.

Enterprise customers have 200 to 500 hosts (decision 2026-11-14). All three parts must stay cheap
and readable at that size.

## Goals

- An open incident carries its relations as data: kind, source, the object it points at and one
  sentence of evidence with numbers. The overview and the incident page show them verbatim.
- "Confirmed" is only ever set from a structural link. "Suspected" never changes severity, never
  notifies, never opens or merges incidents.
- The cause of an incident (the object its hosts share) is stored, so the path to it no longer
  depends on what the dashboard happens to send.
- Opening any incident on the overview shows the latency (and loss) of its hosts.
- A tenant can name its sites, put hosts and probes in them, and the overview shows hosts by site
  once there is more than one.
- An installation that ignores all of this behaves exactly as today.

## Out of scope (deliberately)

| Not in this piece | Why / what it would need later |
|---|---|
| Merging incidents by relation | A Suspected relation is a hint for a person. Merging changes notifications and needs its own design. |
| Root-cause ranking across metrics | "Not available yet" in the honesty table of `SCENARIO.md`. Needs history and evaluation. |
| Charts for non-network rules (disk, service, integration) | Different metric per rule. A chart only where the metric is latency or loss; the others show their last values. |
| Syslog and host matching per site | `source_ip` is ambiguous across sites (multi-tenancy spec, "Syslog"). Sites here only group and filter. |
| Site maps, addresses, geography | A site is a name and a set of hosts. Nothing else is needed for the overview. |
| Notifications that mention relations | Notification text stays as it is. |

## Part A: relations on incidents

### Data model (migration 054)

```
incident_relations
  id             PK
  tenant_id      FK tenants          (TenantScoped)
  incident_id    FK incidents ON DELETE CASCADE
  kind           text  'confirmed' | 'rule' | 'suspected'
  source         text  'topology' | 'probe' | 'integration' | 'rule' | 'syslog' | 'baseline' | 'incident'
  subject_kind   text  'host' | 'probe' | 'integration' | 'incident' | null
  subject_id     int   null
  summary        text  one sentence, English, shown verbatim
  detail         json  numbers behind the sentence (counts, window, baseline)
  first_seen_at  timestamp
  last_seen_at   timestamp
  UNIQUE (tenant_id, incident_id, kind, source, subject_kind, subject_id)

incidents
  + cause_kind   text  null   'host' | 'probe' | 'integration'
  + cause_id     int   null
```

`cause_*` is the one object the incident's hosts share (a parent switch, a silent probe, a failing
integration). It is null when there is none. The overview builds the path from the cause to the
topology root on the server, so it no longer depends on the 12 parents in `topology.parents`.

### Who writes what

| Kind | Written by | When | Example summary |
|---|---|---|---|
| **rule** | `_find_or_create_incident`, for every incident | on create and on each update of the same incident | "Rule multi_host_down: 7 hosts offline in 10.10.20.0/24 for 2 cycles." |
| **confirmed** | the rules that use structure: `upstream_failure` (failed parents), `multi_host_down` (shared parent if all hosts have one), `host_down` behind a silent probe (same probe), `integration_host` (same integration) | on create and update | "All 7 hosts share the parent SW-ZH-CORE-02 in the topology (UniFi)." |
| **suspected** | a new pass at the end of `run_correlation`, only for incidents opened in the last 30 minutes | each correlation run, idempotent | "fw-zh-01 filterlog +420 % against its Friday 14:00 baseline, 3 min before the onset." |

Rules for "suspected":

- Window: from 10 minutes before the incident's onset to 10 minutes after it (owner decision 2).
- Signals that count: a syslog spike or log anomaly (`syslog_spike`, `log_anomaly`,
  `content_anomaly`) on a host that is *not* structurally related; another open incident in the
  same site; an integration that started failing in the window.
- At most three per incident, strongest deviation first. The summary names the number and the
  baseline it is compared with. No number, no relation.
- Never written for an incident without recorded hosts (`host_ids` null).

A relation that stops being true is kept with its `last_seen_at`; the API marks it `stale` when
`last_seen_at` is older than two correlation runs. Nothing is deleted while the incident is open.

### API (additive)

- `GET /api/v2/dashboard`: each `incidents.items[]` gains
  - `relations[]` with at most 4 entries: `kind`, `source`, `summary`, `subject {kind, id, name}`,
    `stale`. Ordered confirmed, rule, then suspected.
  - `cause {kind, id, name, state, path: [{id, name, state}]}` or null.
- `GET /api/v1/incidents/{id}`: full `relations[]` and `cause`.
- Older clients ignore the new fields. The tests in `test_dashboard_v2.py` get the new keys.

### Frontend

`lib/overview.ts:incidentCause` is replaced by the server's `cause`. The relation pills and the
evidence lines come from `relations[]`. "Suspected" pills look as in the prototype (dashed
outline). Phase 2's client-side derivation is removed, not kept as a fallback, so there is one
source of truth.

## Part B: a chart for the incident you open

- New endpoint `GET /api/v2/incidents/{id}/latency?hours=2` (tenant-scoped, readonly role). It
  returns the shape of `LatencySection` for that incident. `section_latency` and the endpoint share
  one builder in a new `services/incident_metrics.py`.
- Only for incidents whose rule is about reachability (`host_down`, `multi_host_down`,
  `upstream_failure`, `precursor_observed` on ping). Other rules return `204` and the UI shows
  their last values instead of a chart.
- At most 50 hosts per incident, sampled by worst state (today: `LATENCY_MAX_HOSTS`). Packet loss
  (`failed / (ok + failed)` per minute) is added to the points, because a chart of a link that
  drops packets shows nothing in latency alone.
- The overview fetches it when an incident is opened. The dashboard keeps `latency` for the worst
  incident, so the first one opened needs no extra request. Cache 30 s, like the dashboard.

## Part C: sites (B-14)

### What exists

`sites (id, tenant_id, name, created_at)` and `site_id` on `ping_hosts`, `agents`,
`agent_install_tokens`, `subnet_scan_schedules`. Enrolment uses the tenant's default site. Nothing
reads `site_id` yet.

### What is added

- **API** `GET/POST/PATCH/DELETE /api/v1/sites` (admin for writes). Deleting a site moves its
  hosts and probes to the default site. The default site cannot be deleted.
- **Assignment**, in this order of precedence:
  1. a site set on the host itself;
  2. the site of the probe that checks the host (owner decision 4);
  3. the default site.

  The effective site is computed, the stored `site_id` is only the explicit choice. The host form
  and the bulk actions on the hosts list set it, the probe form sets the probe's.
- **Read model:** a `sites` section in `GET /api/v2/dashboard`:
  `[{id, name, host_count, by_state, probes: [{id, name, fresh}], worst}]`. Empty when the tenant
  has only the default site.
- **Overview:** "Hosts by site" replaces "Hosts by checker" as soon as there are two or more
  sites. A silent probe still shows its hosts as no data, now inside their site.
- **Filters:** `site` as a filter on the hosts list and the topology page.

### Core or `ee/`

Sites are in the core (owner decision 3). A Swiss SME with branches needs them without any
multi-tenancy; the MSP parts stay in `ee/`.

## Owner decisions

| # | Question | Recommendation |
|---|---|---|
| 1 | Show "Suspected" relations at all? | Yes, UI only, never in notifications, clearly dashed. Without them the label set is incomplete and the honesty table of `SCENARIO.md` loses a row. |
| 2 | Window and limit for "Suspected" | ±10 minutes around the onset, at most three per incident. |
| 3 | Sites in core or `ee/`? | Core. |
| 4 | Hosts inherit the site of their probe? | Yes, unless a site is set on the host. Most branches have exactly one probe. |
| 5 | Charts for rules that are not about reachability | No chart in this piece. Last values only. |

## Pull requests

1. **Relations, data model and writers for rule and confirmed** (migration 054, `cause_*`,
   `incident_relations`, writers in `correlation.py`, API fields). Tests: one per writing rule,
   tenant isolation of the new table, the dashboard shape.
2. **Suspected pass** in `run_correlation`, with tests that build a baseline and check window,
   limit, wording and "no number, no relation".
3. **Chart per incident:** `services/incident_metrics.py`, the endpoint, loss per minute, and the
   overview fetching it when an incident is opened.
4. **Sites, API and assignment:** routes, effective site, host and probe forms, hosts list filter.
5. **Sites in the dashboard and on the overview:** the `sites` section, "Hosts by site", topology
   filter.

Each PR runs the backend suite (including `test_scheduler_jobs_smoke.py`, which fails on logged
errors), Ruff, the migration check in CI and the frontend checks. Every PR adds to CHANGELOG and
`05-dashboard-api.md`.

## Risks

- **Correlation run time.** The suspected pass reads syslog counts and baselines for recent
  incidents only (opened in the last 30 minutes), with one grouped ClickHouse query per run, as
  `_rule_host_down` already does for error counts.
- **Wrong "Confirmed".** It is only written from links the topology already holds with their
  provenance (UniFi, Proxmox, manual). A wrong manual parent gives a wrong "Confirmed"; the
  summary names the source so a person can see why.
- **Stale relations.** Marked `stale` instead of deleted, so the page never shows evidence that
  silently changed.
- **Sites changing the meaning of probes.** The groups section stays as it is, "Hosts by checker"
  stays for single-site tenants, nothing changes until a second site exists.
