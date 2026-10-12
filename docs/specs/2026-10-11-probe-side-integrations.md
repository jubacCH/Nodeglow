# Probe-side integrations

Status: design · 2026-10-11, revised after review the same day — not implemented. Owner decision 4 of
[the multi-tenancy design](2026-10-10-multi-tenancy-design.md#owner-decisions-2026-10-11):
built in parallel to tenancy phase 9.

## Why

Every integration collects from the backend process. `run_integration_checks`
in `backend/scheduler.py` decrypts each config, instantiates the integration
class and awaits `collect()`, which opens connections from the backend
container to the target. On-prem that is fine: the backend sits in the
customer's network.

The SaaS cannot do it. Proxmox at `https://10.0.0.5:8006`, a UniFi controller,
a Synology, a UPS behind NUT, a BMC: all of them sit in RFC1918 space behind
the customer's firewall, and the SaaS reaches none of them. The tenancy spec
spells out the second half of the problem ("SaaS reachability and SSRF"): in
multi-tenant mode the core must only ever connect to public addresses,
otherwise a tenant who types `http://10.1.2.3/` makes the SaaS probe *its own*
infrastructure. Probe-side integrations are what makes a SaaS customer's
integrations work at all.

The probe already exists (`2026-08-28-remote-probes-design.md`): an agent
with `is_probe`, enrolled into exactly one tenant and site, running ICMP, TCP
and HTTP checks for hosts assigned to it and reporting over its heartbeat.
That spec listed probe-side integrations as a non-goal. This one makes them
the goal.

## Goals

- Every integration whose target lives in a customer network can be collected
  by a probe in that network, with the same data in the UI, the same alerts,
  the same auto-import and the same bandwidth series as when the core collects
  it.
- The SaaS never opens a connection into a customer network, and never needs a
  VPN, a port forward or an inbound rule at the customer.
- Collector logic exists **once**. Adding or fixing an integration remains
  "edit one Python file".
- A probe that stops reporting makes its integrations `unknown`, never their
  last value (the remote-probes rule, extended to integrations).
- The blast radius of a compromised SaaS stays bounded: the probe is not a
  general-purpose door into the customer network.
- Existing direct integrations keep working unchanged. Moving one to a probe is
  a per-config choice, not a migration every install has to make.

## Non-goals

- **Several probes per site with failover.** One probe per config to begin
  with; HA is sketched only so the data model does not block it.
- **Probe-side subnet scans.** Same transport, different job; listed under
  "Effort" and left to its own change.
- **Probe-side syslog.** Tenancy phase 10. This design shares the transport
  rules with it but does not build it.
- **Arbitrary user-defined collectors** or scripting on the probe.
- **Billing for probe traffic.** Quotas are named as hooks (tenancy phase 14).

## What exists today

Facts this design builds on, taken from the code on `main` (2.0.0).

### Integrations

`backend/integrations/` holds 19 integrations, auto-discovered by
`integrations/__init__.py`. Each subclasses `BaseIntegration`
(`integrations/_base.py`):

- `config_fields` — a `ConfigField` list; secret fields are `field_type =
  "password"` and/or `encrypted=True`. The whole dict is stored encrypted in
  `integration_configs.config_json` (`services/integration.py:encrypt_config`,
  `models.base.encrypt_value` with the installation `SECRET_KEY`).
- `collect() -> CollectorResult(success, data, error)` — the network part.
- `parse_alerts(data)`, `get_dashboard_summary(data)`, `get_detail_context` —
  pure functions over the snapshot data, run in the backend.
- `on_snapshot(data, config, db)` — a hook after a successful snapshot, with a
  database session (host auto-import, backup sync).
- `default_interval_seconds` (60; speedtest 3600), optional
  `collect_timeout_seconds`.

The transport is already concentrated: **17 of 19 integrations** make every
request through `utils.net_safety.safe_async_client` (httpx with the
connect-time SSRF guard). The exceptions are `nut.py` (raw TCP to the NUT
daemon via `asyncio.open_connection`) and `speedtest.py` (runs the Ookla
`speedtest` binary or the legacy `speedtest-cli` via `subprocess`).

The `collect()` paths import nothing from the backend except `utils.net_safety`
and `integrations._base` — and `_base` imports `fastapi.APIRouter` only for
`get_router()`, which no integration overrides. The database is touched only in
`on_snapshot` (Proxmox: `import_proxmox_hosts` + `sync_proxmox_backups`; UniFi:
host import, with a reverse-DNS lookup `socket.gethostbyaddr` in the backend)
and in `integrations/phpipam.py:sync_phpipam_hosts`, which the settings router
(`routers/settings/general.py`) calls with a live API pass of its own.

Other places that talk to an integration target outside `collect()`:

- `POST /api/v1/integrations/proxmox/{id}/deploy-agent` (`routers/integrations.py`)
  calls the Proxmox API and then **SSHes from the backend** into the node with
  the stored `ssh_private_key` and runs, as root through `pct exec` in every
  running LXC, `curl -sSL {nodeglow_url}/install/linux | bash` — a script the
  server itself serves.
- `services/backup_monitor.py` (UNAS backups) works from the stored snapshot
  only — no live call.
- Bandwidth series: `services/bandwidth.py:extract_proxmox_bandwidth` /
  `extract_unifi_bandwidth` derive rates from successive snapshots and write
  ClickHouse `bandwidth_metrics`, stamped with the config's tenant.

### Scheduling

`run_integration_checks` is a `fleet_job` (tenancy phase 5): every tick, each
due tenant runs concurrently in its own `tenant_scope`; collections share one
`FairShareLimiter` (`NODEGLOW_INTEGRATION_CONCURRENCY`, 8) with round-robin
across tenants; each `collect()` has a timeout (60 s, speedtest 180 s);
`_inflight_cfgs` keeps a slow config from being polled twice; snapshot writes
are serialised (`_snapshot_write_lock`). Due-ness is
`integration_schedule.is_due(last snapshot timestamp, now, effective_interval)`,
with the tenant's `proxmox_interval` as fallback cadence, floored by the
installation key `proxmox_interval_min`.

**Cluster groups** (`cluster_group`, auto-filled from Proxmox `cluster_name`):
all members of a group are collected in one *unit*; in id order, the first
successful member is primary and writes data, the others write a
`[standby:duplicate-of-primary]` marker. This relies on all members being
collected in the same pass — which a probe delivering asynchronously breaks
(see "Cluster groups" below).

### Probe and agent

- Rust agent (`agent/src`): a static musl binary for Linux (~3.3 MB) and a
  Windows exe (~2.8 MB), `opt-level = "z"`; installed by a root/Administrator
  installer and run as a privileged service. Outbound-only, TLS verification
  on by default, bearer token (stored as an HMAC hash in the backend).
- Heartbeat `POST /api/agent/report` every 30 s (agent default). The response
  carries `config`, an optional one-shot `command` (`agents.pending_command`; the
  agent knows only `uninstall`) and, for probes, `checks` — the host
  assignments. The request carries `check_results`. The agent's HTTP client has
  a 15 s timeout; the endpoint has no explicit body-size limit.
- `agent/src/checks.rs`: ICMP/TCP/HTTP(S) checks, at most 64 concurrent,
  undelivered results bounded at 1000, oldest dropped first.
- `routers/agents.py:_handle_report` runs in the agent's `tenant_scope`;
  `_record_probe_results` accepts results only for hosts assigned to that probe.
- `services/probes.py`: per-probe freshness (`staleness_window` = interval × 3,
  at least 120 s; results get one interval more), the `unknown` host state, an
  incident per silent probe — but only for probes with **hosts** assigned
  (`problems_for` counts hosts).
- Auto-update: the agent verifies SHA-256 and, **only when `update_public_key`
  is set** (`agent/src/updater.rs`), an Ed25519 signature. The signing key is
  **held by the server** (`services/agent_signing.py`,
  `DATA_DIR/agent_update_ed25519.key`); the agent gets the public key from the
  install script the same server generates. The agent runs as root and would
  supervise the collector runtime, so whoever holds that key controls what the
  probe runs (see "Signing").
- Suspension: `_authenticate_agent` returns `None` when the agent's tenant is
  not active, so from the first day of a suspension every heartbeat and every
  other `/api/agent/` call of that tenant's probes is refused.

### Core-side jobs that ignore `probe_id`

Only the ping job filters with `probes.core_checked`. SNMP polls
(`services/snmp.py` shells out to net-snmp: v2c `snmpget`/`snmpwalk` with the
community string on the command line, v3 `snmpget` only — no v3 walk — with
the auth and priv passphrases on the command line), SSL expiry (`update_ssl_expiry`), port
discovery and DNS resolution (`resolve_host_dns`) run from the core for every
host, including hosts a probe checks. In the SaaS they would fail (or reach
SaaS-internal addresses). They belong to the same piece of work and are covered
as native primitives below.

## Architecture options

All four keep the backend as the owner of configuration, storage, alerts,
hooks and UI. They differ in where `collect()` runs and what crosses the wire.

### A — the probe runs the Python collectors in an embedded runtime

The probe downloads a **collector pack**: a pinned CPython runtime
(python-build-standalone, or the embeddable distribution on Windows; roughly
20–30 MB compressed), httpx and its dependencies, `utils/net_safety.py`,
`integrations/_base.py` and the integration modules. The Rust agent supervises
it as a child process and talks to it over stdin/stdout (JSON lines). The agent
receives assignments, the runtime runs `collect()`, the agent delivers the
`CollectorResult`s. Parsing for alerts, hooks and storage stay in the backend.

- Reuses every collector unchanged; no second implementation.
- Collection keeps working when the SaaS is unreachable; results are buffered.
- What leaves the probe is a shaped snapshot of a known API, not raw responses.
- Probe footprint grows by a runtime (estimate: 60–80 MB on disk, 40–60 MB RSS;
  to be measured in P0), downloaded only once integrations are assigned.
- A second artifact to build, sign, ship and update per platform; version skew
  between pack and backend must be handled.
- speedtest still needs a native binary; NUT works as-is (asyncio TCP).
- The agent ships a static musl build; python-build-standalone's static musl
  runtime cannot load C extension modules. The pack's dependencies (httpx,
  httpcore, h11, anyio, certifi, idna, sniffio) are pure Python — P0 confirms
  it, or the Linux pack uses the glibc runtime instead.

### B — re-implement the collectors in Rust

Each integration's `collect()` (and the shaping into `data`) is ported to the
agent.

- Smallest footprint, one binary, memory safe, no runtime.
- Two implementations of every collector. The `data` shape is a contract read
  by `parse_alerts`, `get_dashboard_summary`, `on_snapshot`, the bandwidth
  extraction and the frontend; every fix or vendor API change lands twice, and
  drift is silent (a missing key renders as an empty card, not as an error).
- About 4 700 lines of Python collectors become an estimated 6 000–8 000 lines
  of Rust plus fixtures: months of work, and every new integration needs a Rust
  developer.
- Releases couple: the backend cannot use a new field until every probe runs
  the matching agent.

### C — the probe as a generic proxy for the backend's collectors

The collectors keep running in the backend. For a probe-assigned config,
`safe_async_client` is swapped for an httpx transport that sends each request
(method, URL, headers, body) to the probe over a persistent channel (WebSocket
or long-poll); the probe performs it and returns status, headers and body. NUT
needs a TCP-stream primitive; speedtest needs a native job anyway.

- Fastest route to "everything works": the transport is already one function
  for 17 integrations. Zero drift; the collector version always equals the
  backend version.
- **The probe becomes a programmable door into the customer network.** Whoever
  controls the backend — a compromised SaaS, a cross-tenant bug, an operator
  without support approval (owner decision 8) — can send arbitrary requests to
  any address the probe can reach and **read the full response bodies**.
  Today's probe returns success and latency only. That is a qualitative
  escalation, and exactly what a customer's security review will refuse.
- Secrets must be in plaintext in the backend for every request (headers are
  built there) — incompatible with sealing credentials to the probe.
- No collection while the channel is down; nothing to buffer.
- Every request is a round trip SaaS ↔ customer (a Proxmox collect makes tens of
  calls). Needs a persistent connection per probe, routed to the scheduler
  leader across workers (Redis pub/sub or sticky routing) — new stateful
  infrastructure in the SaaS.
- A probe-local allowlist narrows it, but within the list the backend still
  decides what is requested, and still sees the responses.

### D — hybrid: Python collectors on the probe, native primitives in Rust

Option A's execution model for **API collectors** (everything that is "log in,
call a vendor API, shape the result"), plus native Rust for the small set of
**socket-level primitives** that are declarative, high-volume or need a binary:

| In the collector runtime (Python) | Native in the agent (Rust) |
|---|---|
| the 17 HTTP integrations, NUT | ICMP / TCP / HTTP host checks (exist) |
| shaping into `data` exactly as today | SNMP GET/WALK v2c and v3 (new on the probe; today's core has v2c GET/WALK and v3 GET only; the backend keeps MIB/OID logic) |
| | speedtest at the site (wraps the binary, owns its schedule) |
| | TLS certificate expiry, TCP port probe, DNS resolution for assigned hosts |

The rule for "native": the probe is told *what* to measure in a fixed,
declarative form (`host, port, oid list`) and returns a measurement — no
authentication flows, no parsing of vendor JSON. Anything with vendor logic
stays Python, written once.

- The same single source of truth as A for all vendor logic.
- Host-level primitives do not need the runtime: a probe that only checks hosts
  and polls SNMP stays a 3 MB binary.
- SNMP on the probe no longer depends on the net-snmp CLI (absent on Windows)
  and no longer puts the community string or v3 passphrases on a command line,
  where every local user can read them.
- The same costs as A for the runtime (second artifact, skew handling).

### Scored comparison

Scores 1 (poor) to 5 (good); weights in percent.

| Criterion | Weight | A | B | C | D |
|---|---:|---:|---:|---:|---:|
| Security: blast radius of a compromised backend, secret exposure | 25 | 4 | 5 | 1 | 4 |
| One implementation, no drift | 15 | 5 | 1 | 5 | 5 |
| Offline behaviour, failure isolation | 15 | 5 | 5 | 1 | 5 |
| Time to first useful SaaS integration | 10 | 3 | 1 | 4 | 3 |
| Cost of each future integration | 10 | 5 | 1 | 5 | 5 |
| Probe footprint, platforms | 10 | 2 | 5 | 5 | 3 |
| SaaS operational complexity | 10 | 4 | 4 | 2 | 4 |
| Acceptance in a customer's security review | 5 | 4 | 5 | 1 | 4 |
| **Weighted (of 100)** | | **82** | **70** | **56** | **84** |

Reading the numbers: A and D are close by construction — D *is* A plus a rule
for what goes native. The decision that matters is the one between the column
pairs: **run the existing Python collectors on the probe**; do not tunnel the
backend's requests (C) and do not rewrite them (B). C wins on time to market
and loses on the two things the SaaS is sold on — Swiss hosting with
customer-controlled access (decisions 7 and 8). B is the optimum for footprint
only and the worst for velocity.

The security score of 4 for A and D assumes rule 4 below actually holds, i.e.
that a compromised SaaS cannot change what runs on the probe. With today's
server-held agent update key it does not (see "Signing"): a compromised server
can sign an agent binary that skips every other check, and A/D drop to the
level of C for that threat. Release-key signing of agent updates is therefore
a precondition of P2 in the SaaS, not an option.

## Recommendation

**Option D.** API collectors run as today's Python code in a supervised,
signed collector runtime on the probe; host-level primitives (existing checks,
SNMP, TLS expiry, port probe, DNS, speedtest) are native in the agent. The
probe owns the schedule for what it is assigned and delivers finished
`CollectorResult`s; the backend keeps alerts, hooks, storage and UI, through
**one** write path shared with core-run collections.

Design rules that follow:

1. A config is collected by exactly one party, named explicitly by its
   `collector` column: the core (`collector = 'core'`) or a probe
   (`collector = 'probe'`, with `probe_id`). The core never collects a config
   whose collector is a probe — also not when that probe was deleted and
   `probe_id` became NULL — and never fills in for a silent probe.
2. No backend code path needs a decrypted integration secret for a
   probe-assigned config (so credentials can be sealed to the probe later).
3. The probe returns shaped data of known collectors, never raw responses to
   backend-chosen requests.
4. Code that runs on the probe is signed by a key the SaaS server does not hold.
   This covers the collector runtime and pack **and the agent binary itself**
   (its self-update), and the verifying public key is compiled into the agent,
   never delivered by the server. Until agent updates are release-signed with
   mandatory verification (P2 precondition, see "Signing"), rule 4 does not
   hold. Independently of that, no probe command ever runs code or scripts the
   server supplies (see "Proxmox deploy-agent").
5. Results carry the time they were collected, not the time they arrived.
6. Every result — core or probe — is accepted only if the `config_rev` and the
   collector it was started with are still the config's current ones.

## Design

### Collector split (backend, no behaviour change)

- `integrations/_base.py`: `APIRouter` only under `TYPE_CHECKING`; the module
  must import with nothing but the standard library and httpx.
- A static test pins the collect boundary: importing an integration module
  pulls in only `integrations.*`, `utils.net_safety` and an allowlist of
  third-party packages (httpx, httpcore, anyio, certifi, …). An import of
  `models`, `services`, `database` or `fastapi` outside `on_snapshot` or an
  explicitly backend-only function fails it.
- `sync_phpipam_hosts` splits into "fetch" (part of the `collect()` data) and
  "apply" (backend, from the snapshot). The settings route then works from the
  latest snapshot, or requests an on-demand collect through the probe.
- UniFi's reverse-DNS lookup moves into `collect()` (enrichment where the
  customer's DNS is), so `on_snapshot` needs no network.
- `services/integration_ingest.py` takes `_apply_result` and its helpers out of
  `scheduler.py`: snapshot, cluster-group decision, bandwidth (with an explicit
  `timestamp = collected_at`), `on_snapshot`. The core scheduler and the probe
  ingest endpoint both call it.
- A new class attribute `network_scope`:
  - `"site"` — must run where the target is (speedtest: measuring the SaaS's
    uplink is meaningless);
  - `"cloud"` — a public SaaS API (Cloudflare): runs in the core, never needs a
    probe;
  - `"any"` (default) — decided per config by the resolved target address.

### Data model

One migration (the next free revision when it is built; this design track adds
none):

```
integration_configs.collector   VARCHAR NOT NULL DEFAULT 'core'  -- 'core' | 'probe'
integration_configs.probe_id    INT NULL, tenant_fk -> agents ON DELETE SET NULL
integration_configs.config_rev  INT NOT NULL DEFAULT 1   -- bumped on every config change
CHECK (collector = 'probe' OR probe_id IS NULL)
snapshots.collected_by          INT NULL                 -- probe id, NULL = core
snapshots.collected_at          DATETIME NULL            -- probe clock, corrected
```

`probe_id` is a composite tenant FK like `ping_hosts.probe_id`, so a config can
never point at another tenant's probe (RLS and the composite key both refuse
it); the config's site follows from the probe.

**Who collects is `collector`, not the nullness of `probe_id`.** Copying
`ping_hosts.probe_id` alone would make `ON DELETE SET NULL` hand a deleted
probe's configs to the core — on-prem the core would start polling targets it
was never meant to reach, in the SaaS it would try private targets or fail on
S2-sealed secrets it cannot open. With the explicit column, deleting a probe
leaves its configs at `collector = 'probe', probe_id = NULL`: state
**"needs a probe"**, shown as `unknown`, an incident for the tenant, and the
core still skips them (rule 1). Only an explicit "Move to server" sets
`collector = 'core'` (and bumps `config_rev`). In addition, deleting a probe
through the API or UI lists its assigned configs and requires a confirmation
naming what becomes unassigned. (Hosts have the same `SET NULL` hand-over today
via `ping_hosts.probe_id`; aligning them is a separate change.) Both tables are already
`TenantScoped` with RLS and need no new policy, but `migrate.py`'s
re-application of the RLS classification and the classification test must
still pass, and the isolation walk's `_foreign_refs` must learn
`integration_configs.probe_id`.

`snapshots.timestamp` stays the authoritative "as of" time and is set from
`collected_at`; `collected_by` and the arrival lag go to the status API.

No table for commands in the first cut (see "Command channel"). The purge
tombstone `purged_agent_tokens` (see "Tenant suspension and purge") is
installation-level: not `TenantScoped`, no tenant id, classified as global in
the classification test, so `migrate.py` applies no tenant policy to it.

### Wire contract

Probe and backend stay on the agent token; there is no new credential. New
endpoints live under `/api/agent/` (already in `_AUTH_SKIP_PREFIXES`,
authenticated by their own token), run in the probe's `tenant_scope`, and are
listed in `TENANTLESS_ROUTES` of `tests/test_routers/test_tenant_isolation.py`
with that reason — plus isolation tests of their own (a probe of tenant A
naming a config of tenant B is dropped, as `_record_probe_results` does for
hosts).

**Heartbeat** (existing): the response gains a small revision marker, so the
heartbeat stays cheap and never carries secrets — neither integration secrets
nor the credentials of native primitives (SNMP communities, v3 passphrases):

```json
"integrations": {"rev": "c7f1…", "pack": {"version": "2.1.0", "sha256": "…"}}
```

The request gains the runtime's state (see "Observability").

**Assignments** — `GET /api/agent/integrations`, fetched when `rev` changed:

```json
{"rev": "c7f1…",
 "configs": [{"config_id": 17, "config_rev": 5, "type": "proxmox", "name": "pve-zrh",
              "interval_seconds": 60, "timeout_seconds": 60,
              "config": {"host": "https://10.0.0.5:8006", "verify_ssl": true, "token_id": "…"},
              "secrets": {"token_secret": {"enc": "plain", "value": "…"}}}],
 "native": {"snmp": [{"job_id": 4, "config_rev": 2, "host": "10.0.0.20", "port": 161,
                      "version": "v3", "oids": ["1.3.6.1.2.1.1.3.0", "…"],
                      "secrets": {"auth_password": {"enc": "sealed", "value": "…"},
                                  "priv_password": {"enc": "sealed", "value": "…"}}}],
            "tls_expiry": […], "speedtest": {"interval_seconds": 3600}}}
```

Native primitives that need a credential (today only SNMP, from the
`credentials` table) get it here, in the same `secrets` envelope (`plain` /
`sealed` / `local`) and under the same `rev` gating as integration configs —
never in the heartbeat.

`secrets[*].enc` is `plain` (protected by TLS, phase P2), `sealed` (encrypted
to the probe's key, phase P4) or `local` (a name resolved from the probe's
local secret file). The backend computes `interval_seconds` (config override →
class default → tenant interval, clamped by the installation floor and later
the plan tier) — the probe does not reimplement that policy, but enforces a hard
local floor of 10 s so a bug can never make it hammer a customer's API.

**Results** — `POST /api/agent/integrations/results`, gzip, separate from the
heartbeat (a Proxmox snapshot with backup lists can be hundreds of KB; the
heartbeat must stay small and fast):

```json
{"pack": {"version": "2.1.0", "schema": 1},
 "clock": {"sent_at": "2026-10-11T09:01:13Z"},
 "results": [{"id": "6f0c…", "config_id": 17, "config_rev": 5, "type": "proxmox",
              "collected_at": "2026-10-11T09:01:02Z", "duration_ms": 812,
              "success": true, "data": {…}, "error": null}]}
```

Response:

```json
{"accepted": ["6f0c…"],
 "rejected": [{"id": "…", "reason": "stale_config_rev"}],
 "retry_after": null}
```

Rejection reasons: `stale_config_rev`, `not_assigned`, `too_large`, `schema`.

- `id` makes delivery idempotent: a retry after a lost response is
  acknowledged without a second snapshot.
- A `config_rev` that is not **exactly** the stored one is rejected — older
  (data collected with an old host or old credentials must not overwrite the
  new config's state) as well as newer (a rev the backend never issued).
- The same check applies to **core-run collections** (rule 6): the scheduler
  captures `(config_rev, collector, probe_id)` when it starts a collect and
  passes them to `integration_ingest` with the result; ingest re-reads the
  config under the snapshot write lock and drops the result unless all three
  still match. A core collect that was in flight (up to 60 / 180 s) when "Move
  to probe" bumped the rev therefore writes nothing, and a probe result for a
  config moved back to the server is rejected the same way.
- A `config_id` not assigned to this probe (or belonging to another tenant —
  the lookup runs in the probe's scope) is rejected and counted, like host
  results today.
- Clock: the backend compares `sent_at` with its own time and corrects
  `collected_at` by the offset (bounded); a result "from the future" is clamped
  to the arrival time and the skew is reported.
- Limits: 4 MB uncompressed per result (`too_large` is stored as a failed
  snapshot with that reason — visible, never silently dropped) and 16 MB per
  request; both installation keys.
- The endpoint stores snapshot and bandwidth synchronously and acknowledges;
  `on_snapshot` hooks run afterwards in a task that sets the config's tenant
  itself (tenancy lesson: a background task must not rely on an inherited
  context).

**Native primitives**: assignments (with any credentials) come from the
assignments endpoint above; only their **results** use the existing heartbeat
pattern of `check_results`, with their own result types (values per OID, TLS
expiry days, open ports), because they are small, periodic and secret-free.
Each result carries its job's `config_rev`, checked as for integrations.

### Collector runtime on the probe

- The agent downloads runtime and pack lazily, the first time it is assigned an
  integration (`GET /api/agent/collector/{platform}` and
  `/api/agent/collector-pack/{version}`), verifies SHA-256 and an Ed25519
  signature, and unpacks into its data directory. A probe with host checks only
  never downloads it.
- The pack is built in CI at release, versioned with the backend, and served
  by the backend (like the agent binaries in `backend/static/`). Its Python and
  dependency versions equal the backend image's.
- Supervisor: one runtime process, restarted with exponential backoff; JSON
  lines over stdin/stdout (`{"op": "collect", "config": …}` →
  `{"op": "result", …}`); one collect per config at a time (the probe's
  `_inflight_cfgs`), at most 4 concurrent collects (configurable); the timeout
  is enforced by the agent, which kills and restarts a runtime that stops
  answering.
- Least privilege: the runtime runs as a dedicated unprivileged user on Linux
  (created by the installer; the agent itself runs as root), with no
  capabilities, `NoNewPrivileges` and a private temp dir; on Windows under a
  restricted token. It receives only the configs it collects, over its pipe; it
  never sees the agent token and cannot write outside its working directory.
- The runtime keeps `utils.net_safety`, so the probe applies the backend's SSRF
  rules (loopback, link-local and metadata endpoints refused — relevant when
  the probe itself runs on a cloud VM).

### Scheduling and cadence ownership

| Option | Verdict |
|---|---|
| Backend-driven: the scheduler tick sends "collect X now" | Rejected as the default. Needs a low-latency push for every collect, stops when the link is down, and puts every customer's cadence on one SaaS loop. |
| **Probe-owned, backend-assigned** | **Recommended.** The probe schedules its assigned configs at `interval_seconds` with jitter, as it already runs host checks between heartbeats. |

- `run_integration_checks` collects only `collector = 'core'` configs (the
  `core_checked` equivalent for configs) — rule 1; a `collector = 'probe'`
  config with `probe_id IS NULL` is skipped too ("needs a probe").
- A tenant's fair share and concurrency cap apply at **ingestion** (a per-tenant
  token bucket on results and bytes, tenancy phase 14); on the probe,
  concurrency is a local resource limit.
- On-demand collects ("Test connection", "Collect now", the phpIPAM sync
  button) go through the command channel and return the result to the waiting
  request.

### Command channel

Today a command is one string delivered with the next heartbeat (≤ 30 s). For
on-demand collects and tests that is too slow, and it carries no payload.

- `GET /api/agent/commands?wait=25` — a long-poll the probe keeps open. It works
  through reverse proxies and Cloudflare (well below their idle timeouts),
  needs no WebSocket support, and falls back to the heartbeat command where it
  is unavailable.
- A command: `{"id", "op", "config_id", "expires_at"}` with `op` one of
  `collect`, `test`, `refresh_assignments`, and — on-prem / single-tenant only,
  under the conditions in "Proxmox deploy-agent" below — `deploy_agents`. The
  result of `collect`/`test` comes back through the results endpoint with the
  command id; the UI request waits up to its timeout, then shows "pending".
  `wipe` is not a command on this channel: a probe of a suspended or purged
  tenant cannot authenticate here, so it is delivered by the lifecycle endpoint
  (see "Tenant suspension and purge").
- One backend process: an in-memory waiter per probe. Several workers or HA:
  wake-ups through Redis pub/sub (already a dependency of the leader lock) or a
  short DB poll. If commands need persistence, a tenant-owned `agent_commands`
  table — `TenantScoped`, RLS policies, classified.
- Commands are a closed set. There is no "fetch this URL" command, by design
  (rule 3).

### Credentials

Integration secrets are the customer's credentials to systems in the network
the probe lives in. Three ways to get them there:

| | S1 — pushed over TLS | S2 — sealed to the probe | S3 — local only |
|---|---|---|---|
| Entered | in the SaaS UI (as today) | in the SaaS UI, sealed in the browser with the probe's public key | on the probe (local secret file / CLI) |
| SaaS at rest | encrypted with the tenant key | ciphertext the SaaS cannot open (additionally under the tenant key) | a name only |
| SaaS can read it | yes (with the tenant key) | not from what it stores; a compromised SaaS **frontend or backend** can still capture new input (see below) | no |
| Protects against | transport eavesdropping | a leaked or passive database / backup, an operator reading stored data | also a compromised SaaS (frontend, backend, operator) |
| Probe at rest | encrypted with a probe-local key, or memory only | sealed blob + probe private key | the customer's file |
| Crypto-shred on purge (decision 7) | destroying the tenant key covers the SaaS copy; the probe gets `wipe` | inherent; the probe gets `wipe` | nothing in the SaaS |
| Support access (decision 8) | an approved operator session could in principle reach plaintext | stored secrets stay unreadable; an approved session cannot re-seal them to another key (re-seal needs customer input and a fingerprint confirmation) | cannot |
| Replacing the probe | transparent | secrets re-entered (or sealed to every probe of the site up front) | re-create the local file |
| Effort | S | M (X25519 sealed boxes in browser and agent, re-seal flow) | S |

Recommendation: **S1 first (P2), S2 as the SaaS default (P4), S3 always
available** — the answer for customers who want no credential in the SaaS at
all, and the only option whose threat model includes a compromised SaaS.

**What S2 does and does not deliver.** The SaaS stores and serves the probe's
public key, and it serves the JavaScript that does the sealing. A compromised
SaaS — or anyone who can change the registered key or the frontend — can
substitute its own key or read the plaintext before it is sealed. S2 therefore
protects secrets **at rest** in the SaaS (database, backups, an operator
reading tables, an approved support session), not secrets being **entered**
through a compromised SaaS. To close the key-substitution path as far as the
browser allows:

- **Out-of-band fingerprint confirmation.** The probe shows its sealing-key
  fingerprint locally (installer output, `nodeglow-agent fingerprint`, the
  probe's log). Before the first seal to a probe, and after every key change,
  the form shows the fingerprint the backend serves and the customer must
  confirm it matches what the probe printed; a probe whose key changed is
  marked "key unconfirmed" and nothing is sealed to it until confirmed.
- **Re-seal only from customer input.** There is no server-side or
  operator-triggered re-seal: moving secrets to a new or replacement probe
  means a customer re-entering them (or, with the old probe still alive, the
  old probe decrypting and re-sealing to a key whose fingerprint the customer
  confirmed). A support session can enrol a probe, but cannot get existing
  secrets sealed to it.
- Customers whose threat model includes a compromised SaaS use **S3**.

Interaction with per-tenant envelope keys (decision 7, built in parallel):

- Probe-assigned configs are encrypted exactly like core-run ones. This design
  calls whatever replaces `services/integration.py:decrypt_config` with the
  tenant's data key and must not keep a path that uses `SECRET_KEY` directly.
  The assignments endpoint decrypts in the probe's tenant scope — the same
  place the tenant key is unwrapped for core collections.
- Under S2 the sealed blob sits inside the DEK-encrypted `config_json`. Purge
  destroys the DEK; the SaaS copy is gone either way.
- Suspension and purge reach the probe through the lifecycle endpoint (next
  section), not through the command channel, because a suspended tenant's
  probes can no longer authenticate there.
- Probe-local persistence: S1 secrets may be cached on the probe, so a probe
  that restarts during a SaaS outage keeps collecting — encrypted with a
  probe-local key (DPAPI on Windows, a 0600 key file on Linux). Proposed
  default: on, because surviving an outage is the point of collecting locally
  (open question 4). The cache is dropped on `suspended`, on `wipe` and by the
  self-wipe below.
- The probe's own keys: generated at enrolment (X25519 for sealing; the public
  key goes to the backend with the enrol request, its fingerprint is printed
  locally). Rotation = new key + fingerprint confirmation + re-seal from
  customer input (above).

### Tenant suspension and purge

Owner decision 6: a deleted tenant is suspended for 30 days, then purged. Today
`_authenticate_agent` refuses every agent of a non-active tenant, so without a
dedicated path a queued `wipe` could never be delivered, and a probe would keep
collecting with cached credentials through the whole suspension.

- **Lifecycle endpoint** `GET /api/agent/lifecycle`: authenticated by the agent
  token through its own lookup that also accepts agents of a `suspended` or
  `deleting` tenant and tokens in the purge tombstone. It answers only
  `{"state": "active" | "suspended" | "wipe"}` — no config, no assignments, no
  commands, no tenant data. The agent calls it whenever any other endpoint
  answers `401`/`403`, and every 15 minutes while it is not `active`. All other
  `/api/agent/` routes keep refusing a suspended tenant's agents.
  Classified in `test_tenant_isolation.py` (`TENANTLESS_ROUTES`, reason: agent
  token, lifecycle state only).
- **`suspended`**: the probe stops all collection (integrations, native
  primitives, host checks), drops cached secrets, assignments and buffered
  results, keeps its token and keys, and keeps polling the lifecycle endpoint.
  If the tenant is reactivated within the 30 days, it answers `active`; the
  probe re-fetches assignments (S1 secrets are pushed again, S2 blobs re-served
  from the SaaS copy, S3 is local anyway) and resumes.
- **`wipe`**: issued when purge **starts**, before agent rows are removed. The
  purge first copies each agent's token hash into an installation-level
  tombstone (`purged_agent_tokens`: token hash, purged_at — no tenant id, no
  tenant data; classified as global, not `TenantScoped`), then deletes the
  tenant's rows and destroys its key. For 90 days a tombstoned token gets
  `wipe` from the lifecycle endpoint; the agent deletes assignments, cached
  secrets, its probe keys and buffered results, acknowledges (the
  acknowledgement time is recorded on the tombstone and in the purge report),
  then uninstalls through the existing `uninstall` path. Tombstones are deleted
  after 90 days.
- **Self-wipe**: an agent whose token is refused by every endpoint, the
  lifecycle endpoint included, for 7 consecutive days (configurable locally,
  not by the server) deletes cached secrets, assignments, buffered results and
  probe keys and stops collecting; it does not uninstall itself. This covers a
  probe that was offline through the whole tombstone window, and a probe whose
  agent row was deleted by hand.
- A probe that never comes back keeps only data that was the customer's to
  begin with; the purge report lists the probes that did not acknowledge.
- Tenancy phase 9's purge must implement the tombstone step; the tenancy spec
  links here.

### Proxmox deploy-agent

The Proxmox **deploy-agent** feature is the one place where the backend uses a
secret itself (SSH from the backend). Today it runs
`curl -sSL {nodeglow_url}/install/linux | bash` as root in every running LXC —
a script chosen by whoever controls the server. Turned into a probe command
as-is, it would let a compromised SaaS, a cross-tenant bug or an operator
outside an approved support session (decision 8) run arbitrary code as root in
every container of the customer's Proxmox nodes, which breaks rule 4, the
"not a general-purpose door" goal and decision 8. Therefore:

- **Multi-tenant mode (SaaS): not available** for probe-assigned configs —
  neither from the backend (rule 2, and the backend cannot reach the node) nor
  as a probe command. The UI shows the manual install command for the customer
  to run themselves.
- **Single-tenant / on-prem**: `deploy_agents` exists as a probe command only
  when all of these hold:
  - a **probe-local opt-in** (`allow_deploy_agents = true` in the agent's local
    config, set by the installer or by hand; the backend cannot change it);
  - the probe deploys **its own release-signed agent binary** — the payload is
    the binary plus a fixed install procedure compiled into the agent (`pct
    push`, write the config with the token from the command, install the
    service), verified against the compiled-in release key before anything is
    run; **no server-served script is piped into a shell**;
  - the command carries only data (target node, VMIDs, the enrolment token),
    never a command line;
  - every run is written to the probe's local log and to the audit log.
- Until release-signed agent updates exist (see "Signing"), `deploy_agents` is
  not offered at all.

### Data contract, backpressure and buffering

- **Latest wins per config.** An integration snapshot is state, not a stream:
  the probe keeps at most one undelivered result per config (a newer one
  replaces it). The backend computes bandwidth rates from consecutive
  `collected_at` values, so a gap becomes a longer averaging window, never a
  spike.
- Bounded by bytes (default 32 MB) as well as count; past the bound the oldest
  results are dropped first and counted (`dropped_total`), mirroring
  `PendingResults` for checks.
- Backend backpressure: `429` with `Retry-After` (per-tenant bucket or
  installation overload); the probe backs off, keeps collecting and coalesces.
- Delivery order: oldest `collected_at` first; acknowledgement per result.
- Schema: `pack.schema` names the version of the `data` contract. The backend
  accepts a window `[min_supported, current]`; outside it, results are rejected
  with `schema`, the configs show "probe needs update", and the self-check
  raises it. A shape change bumps the schema and ships a small backend shim for
  the previous version until the window moves on.

### Failure modes

| Failure | What the user sees | Mechanism |
|---|---|---|
| Probe silent (offline, crashed) | its integrations `unknown`, with "last collected by probe X at T"; one incident per probe | per-probe freshness extended to integrations: `problems_for` counts hosts **and** configs; a config is `unknown` when its probe is stale or its newest snapshot is older than `interval × 3 + heartbeat` |
| Probe deleted | its integrations `unknown` ("needs a probe"), incident; the core does **not** take over | `collector = 'probe'` stays, `probe_id` becomes NULL; the core collects only `collector = 'core'` |
| Tenant suspended | the probe stops collecting and drops cached secrets | lifecycle endpoint answers `suspended` |
| Tenant purged | the probe wipes itself and uninstalls; the purge report lists probes that never acknowledged | purge tombstone + lifecycle `wipe`; local self-wipe after 7 days of refused tokens |
| Target unreachable from the probe | integration error, as today | `success=false` with the collector's error |
| Collector runtime crash-looping or missing | configs `unknown` ("collector unavailable on probe X"), incident | runtime state in the heartbeat; never reported as a target failure |
| SaaS unreachable | nothing until it is back, then the backlog oldest first | probe buffer, latest wins |
| Duplicate delivery | nothing | result `id` |
| Config edited while a result is in flight | the old result is discarded | `config_rev` (exact match) |
| Config moved to a probe while a core collect is in flight | the core result is discarded | ingest checks `config_rev` + collector for core results too (rule 6) |
| Config moved to another probe | the old probe's results are rejected | `not_assigned` |
| Version skew pack ↔ backend | "probe needs update" | `schema` window |
| Clock skew on the probe | correct timestamps; skew shown on the probe | `sent_at` offset |
| Snapshot too large | a failed snapshot with the reason | `too_large` |

The core never writes a snapshot for a probe-assigned config — neither "ok" nor
"failed". Absence is shown as absence (remote-probes rule 3).

### Cluster groups

The primary/standby decision assumed all members are collected in one pass. With
asynchronous arrivals (possibly two probes for two nodes of one cluster) the
rule becomes: **the lowest-id member with a successful snapshot younger than
twice its interval is primary**; a result from another member is stored as
standby while that holds, and becomes primary once the primary's freshness
lapses. It is decided in `integration_ingest` under the snapshot write lock for
core and probe results alike, so a mixed group (one member core-run, one on a
probe) behaves the same. Auto-grouping by `cluster_name` stays per tenant (as
tenancy phase 4 keyed it).

### Security

- **AuthN.** The agent bearer token over TLS with verification, as today. mTLS
  is not the primary mechanism: in the SaaS, TLS terminates at the edge proxy,
  and client certificates would have to be forwarded as headers. Instead, from
  P4 the probe signs result batches with an Ed25519 key generated at enrolment
  (proof of possession: a leaked bearer token alone cannot inject data). Token
  rotation by command.
- **Tenant binding.** Tenant and site come from the probe's agent row, never
  from the body; every lookup runs in the probe's `tenant_scope`; RLS backs it.
- **SSRF.** Core side: in multi-tenant mode a config with `network_scope =
  "any"` whose target resolves to a non-public address cannot run in the core
  (saving it without a probe is refused with "needs a probe"; the guarded
  transport repeats the check at connect time) — tenancy phase 14's egress
  restriction. Probe side: `net_safety` in the runtime, plus an optional
  customer-controlled `integration_targets` allow/deny list (CIDRs, hosts) in
  the agent config that the backend cannot change. Default: whatever the
  backend's rules allow.
- **Least privilege at the target.** The docs recommend read-only credentials
  (a Proxmox token with `PVEAuditor`, a UniFi read-only admin, a non-admin
  Synology user with the needed apps, a read-only TrueNAS key, …); the
  integration form says so for probe-assigned configs.
- **Signing.** The collector runtime and pack are signed with a **release key**
  whose public half is compiled into the agent; the SaaS server never holds the
  private key. That alone protects nothing while the agent's **own** update key
  is server-held (`services/agent_signing.py`, `DATA_DIR`) and verification is
  optional (`updater.rs` checks only when `update_public_key` is set, and the
  server's install script delivers that key): a compromised SaaS could sign
  and push an agent binary that skips the pack check. Rule 4 therefore needs,
  **as a precondition of P2 in the SaaS** (P2a in the plan):
  - agent updates signed with the release key in CI; the release public key
    **compiled into the agent binary**, not delivered by the install script or
    any server response;
  - verification **mandatory** for probes (and for every agent of a
    multi-tenant installation): an update without a valid release signature is
    refused, and no server-supplied setting can disable or replace the key;
  - the server-held `agent_update_ed25519.key` remains only for self-built
    on-prem installs (git mode), where server operator and customer are the
    same party; there the operator may set an additional pack/update key in the
    agent's **local** config. In multi-tenant mode such an override is refused.
  - The first install still trusts what the customer downloads; the release
    binaries' signatures and hashes are published outside the SaaS so a
    customer can verify them before installing.

  Until this lands, the spec does **not** claim rule 4: probe-run code is only
  as trustworthy as the SaaS server, D's security score does not apply, and
  `deploy_agents` is not offered.

  **Implemented (P2a, branch `feat/agent-release-signing`):** CI signs both
  binaries (`tools/agent-release-key/sign.sh`, secret
  `AGENT_RELEASE_SIGNING_KEY`), commits `backend/static/<binary>.sig` and
  attaches the signatures to the GitHub Release; the backend relays them as
  `release_signature` / `release_key_id`. The agent's trust mode (release
  only / probe / standard, latched in `update-trust.json`) is described in
  `docs/OPERATIONS.md#agent-updates`. Rule 4 holds for the agent binary once
  the real release public key replaces the placeholder in
  `agent/src/release_keys.rs`; the pre-P2a server signature is still sent so
  older agents (which know no other path) can reach a release-signed build,
  and P2a agents in multi-tenant mode ignore it.
- **No server-supplied code or scripts.** No probe command runs a script or
  command line the server sends or serves; `deploy_agents` (on-prem only,
  probe-local opt-in, release-signed payload — see "Proxmox deploy-agent") is
  the only command that installs anything.
- **Key authenticity for sealing.** S2 relies on a customer-confirmed
  fingerprint of the probe key (see "Credentials"); S2 protects stored secrets
  against a leaked database or an operator, not against a compromised SaaS
  frontend — that threat model is S3's.
- **No raw responses.** The probe returns `CollectorResult`s only. Error
  messages are truncated and redacted (`Authorization`, cookies, the configured
  secret values) before they leave the probe.
- **Logs on the probe** never contain secrets: they are wrapped in a type whose
  `Debug`/`Display` prints `***`.

### Multi-site

- A config is assigned to one probe; the probe's site is the config's site. Two
  branch offices with a UniFi at `192.168.1.10` each are two configs on two
  probes — no ambiguity, because the address is only ever resolved inside its
  site.
- Assignment UI: a "Collected by" selector on the integration form (the server,
  or a probe of the tenant grouped by site), like `probe_id` on hosts.
- Auto-imported hosts (Proxmox guests, UniFi devices, phpIPAM) land in the
  config's site **and are assigned to the same probe** by default — otherwise
  the SaaS would try to ping imported RFC1918 hosts from the core.
- HA later: a probe group per site, assignments with a priority; the
  cluster-group freshness rule above already tolerates two collectors.

### Observability

- The heartbeat request gains:

  ```json
  "integration_runtime": {"state": "running", "pack_version": "2.1.0",
                          "assigned": 5, "buffered": 2, "buffered_bytes": 183000,
                          "dropped_total": 0,
                          "last_errors": [{"config_id": 17, "error": "…", "at": "…"}]}
  ```

  `state` is one of `running`, `starting`, `crashloop`, `absent`.
- Backend metrics: `nodeglow_probe_integration_results_total{tenant,status}`,
  `nodeglow_probe_integration_lag_seconds` (arrival − `collected_at`),
  `nodeglow_probe_integration_rejected_total{reason}`,
  `nodeglow_probe_integration_bytes_total{tenant}`; tenant labels bounded as in
  phase 5 (`NODEGLOW_TENANT_METRIC_LABELS`).
- The status API per config: `collected_by`, `collected_at`, `delivered_at`,
  `state` (`ok` | `error` | `unknown` | `standby`).
- The probe detail page lists its integrations, runtime state and buffer.
- A tracing span `integration.ingest` with `probe_id`, `config_id` and the lag.
- Probe incidents name the integrations they affect.

### Migration path for existing direct integrations

- Nothing changes on upgrade for integrations: every existing config gets
  `collector = 'core'`, `probe_id = NULL`, the core keeps collecting it, and
  single-tenant on-prem installs never have to touch probes.
- Native primitives (SNMP, SSL expiry, port discovery, DNS) are different:
  hosts can already be assigned to a probe (`ping_hosts.probe_id`). They do not
  move with that assignment by themselves (see P5): the core keeps running
  them for a probe's hosts until the probe advertises the matching capability,
  and in single-tenant / on-prem mode the move stays opt-in.
- "Move to probe" on a config: choose a probe → the backend sends a `test`
  command → on success `collector = 'probe'`, `probe_id` is set and
  `config_rev` bumped. "Move to server" sets `collector = 'core'`,
  `probe_id = NULL` and bumps `config_rev`. Snapshots
  keep their `entity_id`, so history, alerts and backup jobs continue. Moving
  back to the server is the same in reverse.
- Bulk: "move all integrations of site X to probe Y".
- **SaaS readiness report** (before tenancy phase 14 turns on the egress
  restriction for a tenant): every config whose target is private and that has
  no probe, every private host the core checks, every SNMP / SSL / port job that
  would run from the core. Instances moving into the SaaS by export/import
  (tenancy phase 12) get the same report on import; configs with a private
  target arrive with `collector = 'probe', probe_id = NULL` ("needs a probe")
  until assigned.
- `speedtest` (`network_scope = "site"`) in multi-tenant mode: on a probe only.
- `cloudflare` (`"cloud"`) stays in the core everywhere.

## Effort per integration

Under the recommendation, a Python integration needs no port. The work is a
fixture-based test that its `collect()` runs inside the pack, a check that it
does not reach into the backend, and whatever it does today outside `collect()`.
Option B is listed for comparison.

| Integration (`name`) | Transport / auth | Scope | Work beyond the generic path | D | B |
|---|---|---|---|---|---|
| Proxmox VE (`proxmox`) | HTTPS, API token | any | deploy-agent: unavailable in the SaaS; on-prem a probe command with local opt-in and a release-signed payload; backup sync and bandwidth already work from data | S + M | L |
| UniFi (`unifi`) | HTTPS, cookie login (UniFi OS / legacy) | any | rDNS enrichment into `collect()`; bandwidth; imported hosts follow the probe | S | M |
| Synology (`synology`) | HTTPS, session login | any | — | XS | M |
| UNAS (`unas`) | HTTPS, login | any | backups already from the snapshot | XS | M |
| TrueNAS (`truenas`) | HTTPS, API key | any | — | XS | S |
| UPS / NUT (`ups`) | raw TCP 3493 | any | runs as-is (asyncio); not covered by `net_safety` → add the same host check | S | S |
| Redfish (`redfish`) | HTTPS, basic | any | BMCs with self-signed certificates: `verify_ssl` honoured on the probe | XS | M |
| Firewall (`firewall`) | HTTPS, key/secret | any | — | XS | M |
| Pi-hole (`pihole`) | HTTP(S), v6 session / v5 key | any | — | XS | M |
| AdGuard Home (`adguard`) | HTTP(S), basic | any | — | XS | S |
| Technitium (`technitium`) | HTTP(S), token | any | — | XS | S |
| phpIPAM (`phpipam`) | HTTPS, app token / login | any | split `sync_phpipam_hosts` into fetch + apply; sync button via command | S | M |
| Nginx Proxy Manager (`npm`) | HTTP(S), login JWT | any | — | XS | M |
| Portainer (`portainer`) | HTTPS, API key | any | — | XS | S |
| Home Assistant (`hass`) | HTTP(S), long-lived token | any | — | XS | S |
| Gitea (`gitea`) | HTTPS, token | any | often public → may stay in the core | XS | S |
| Swisscom Internet-Box (`swisscom`) | HTTP(S) to the local router, login | any (in practice site) | — | XS | M |
| Cloudflare (`cloudflare`) | public API, token | cloud | none: stays in the core | — | — |
| Speedtest (`speedtest`) | Ookla / speedtest-cli binary | site | native job on the probe; binary distribution and licence (open question 1) | M | M |

XS ≈ hours, S ≈ 1–3 days, M ≈ 1–2 weeks, L ≈ 3–5 weeks. Proxmox, UniFi and
Synology are the pilot set: the most installed, and three different auth styles
(token, cookie login, session login).

Native primitives (not integrations, but the same work):

| Primitive | Today | Probe work |
|---|---|---|
| ICMP / TCP / HTTP checks | on the probe (done) | — |
| SNMP polls | core, net-snmp CLI (v2c GET/WALK, v3 GET only) with the community or v3 passphrases on the command line | Rust SNMP v2c and v3 GET/WALK; the assignments endpoint sends `(host, port, oids)` plus the credential in the `secrets` envelope (never the heartbeat); M |
| SSL expiry | core, for every host | TLS handshake on the probe for its hosts; S |
| Port discovery | core, for every host | bounded TCP connect scan on the probe; S |
| DNS resolution of hosts | core | resolve on the probe (the customer's DNS); S |
| Subnet scans | core | separate change (non-goal here); M |

**Capability gate for native primitives.** A primitive moves to a probe only
when that probe advertises it (`capabilities: ["snmp:1", "tls_expiry:1",
"ports:1", "dns:1", …]` in the heartbeat, see "Ordering with the tenancy phases"). Until then the core
keeps running it for the probe's hosts exactly as today, so an agent that has
not auto-updated (update failed, signature mismatch, offline box) loses
nothing. In single-tenant / on-prem mode the move is additionally opt-in per
probe (and overridable per host), because it changes behaviour — DNS, for
example, then resolves through the probe's resolver instead of the server's.
In multi-tenant mode the move is the default for capable probes (the core
cannot reach the targets anyway). Once a primitive runs on a probe it follows
the probe freshness rules: an SSL-expiry, SNMP, port or DNS job with no result
past its freshness window turns `unknown` and raises an incident instead of
going silent, so a certificate cannot expire unnoticed because a probe stopped
reporting.

## Phased plan

| # | Step | Where | Effort |
|---|---|---|---|
| P0 | Spike: build a pack (python-build-standalone + httpx + integrations) for linux-x86_64 and windows-x86_64; measure size and RSS; run Proxmox/UniFi/Synology `collect()` from it against recorded fixtures; check the speedtest licence | — | S |
| P1 | Collector split (backend only, no behaviour change): `_base` without FastAPI, boundary test, shared `integration_ingest` write path with the exact `config_rev` + collector check for core results, `network_scope`, phpIPAM/UniFi splits; migration for `collector`, `probe_id`, `config_rev`, `collected_by`, `collected_at`; the core collects only `collector = 'core'` | core | M |
| P2a | **Precondition of P2 in the SaaS:** release-key signing of agent updates in CI, release public key compiled into the agent, verification mandatory for probes and in multi-tenant mode, no server-delivered key override | core (agent + CI) | S |
| P2 | Probe runtime: download + signature check (release key), supervisor, local scheduler, latest-wins buffer, assignments and results endpoints, S1 secrets, S3 local references, per-probe freshness and `unknown` for integrations, runtime state in the heartbeat; lifecycle endpoint (`suspended` / `wipe`), purge tombstone hooked into the phase 9 purge (or added to it when phase 9 lands later), local self-wipe; pilot Proxmox, UniFi, Synology | core (agent + backend) | L |
| P3 | Long-poll command channel; UI: "Collected by", move to probe / server, "needs a probe" state and probe-deletion confirmation, test via probe, status and lag; the remaining HTTP integrations and NUT (a fixture test each); auto-imported hosts follow the probe; readiness report | core + frontend | M |
| P4 | Sealed credentials (S2): probe key at enrolment with a locally shown fingerprint, customer confirmation before sealing, browser sealing, re-seal from customer input only; signed result batches | core | M |
| P5 | Native primitives behind the capability gate: SNMP (credentials via the assignments endpoint), SSL expiry, port discovery, DNS on the probe — the core keeps running each for a probe's hosts until the probe advertises it, opt-in per probe on-prem, `unknown` + incident past freshness; speedtest as a site job; on-prem only: Proxmox `deploy_agents` with probe-local opt-in and a release-signed payload (requires P2a) | core (agent + backend) | M + M |
| P6 | SaaS enforcement (with tenancy phase 14): the core refuses private targets in multi-tenant mode; per-tenant ingestion quotas; plan-tier interval floors | core + ee | S |

S ≈ days, M ≈ 1–2 weeks, L ≈ 3–5 weeks for one developer. P0–P3 (a SaaS
customer's Proxmox, UniFi, NAS and HTTP integrations working through a probe)
is about two months; P0–P6 about three and a half.

Ordering with the tenancy phases: P1 can start now (backend only, independent
of phase 9). P2 touches the agent's main loop, as phase 10's syslog relay does;
both add a probe capability next to `checks`, so they should agree on one shape
(e.g. `capabilities: ["checks", "syslog", "integrations:1", "snmp:1",
"tls_expiry:1"]` in the heartbeat) before either merges. P2 in the SaaS
depends on P2a (release-signed agent updates) and needs the phase 9 purge to
write the agent tombstone. P4 depends on the envelope-key work. P6 is part of
phase 14.

**Open Core.** Probe-side integrations are core (AGPL): probes, sites and
probe-based syslog are core in the tenancy spec's table, and a single-tenant
multi-site install needs this as much as the SaaS does. Plan-tier quotas and
interval floors for probe ingestion are ee (with phase 14's plan limits).

## Testing

- Boundary test (P1): integration modules import only the allowed set; hook
  modules open no sockets and use no `safe_async_client`.
- Contract tests: for each integration, recorded fixture responses → `collect()`
  run (a) in the backend and (b) through the pack runner; the `data` must be
  identical. This is D's drift guard and the place a schema change is caught.
- Ingest tests: idempotency, `config_rev` (older **and** never-issued newer
  revs rejected), `not_assigned` (including another tenant's config id), clock
  correction, size limits, the cluster-group freshness rule with interleaved
  arrivals, `unknown` while the probe is stale; the **move-during-collect
  race**: a core collect started at rev N with `collector = 'core'` finishes
  after "Move to probe" set rev N+1 → no snapshot written, and the reverse for
  a probe result after "Move to server".
- Probe deletion: deleting a probe with assigned configs leaves them
  `collector = 'probe', probe_id = NULL`; `run_integration_checks` collects
  none of them; they show "needs a probe" / `unknown`.
- Lifecycle: a suspended tenant's probe gets `401` on the heartbeat, the
  assignments and the commands endpoints but `{"state": "suspended"}` from
  `/api/agent/lifecycle`; after reactivation it gets `active`; a purged
  tenant's tombstoned token gets `wipe`, an unknown token `401`; the tombstone
  holds no tenant id; the endpoint returns nothing beyond the state. Agent side:
  `suspended` stops collection and drops the secret cache; `wipe` deletes
  secrets, buffer and keys; self-wipe after the local refusal window.
- Heartbeat secrecy: no heartbeat response contains a credential value (SNMP
  community, v3 passphrase, integration secret) — asserted on a probe with
  SNMP and integration assignments.
- Capability gate: a probe without `tls_expiry:1` leaves SSL expiry for its
  hosts in the core; with it (and the on-prem opt-in), the core skips them and
  a missing result past the freshness window yields `unknown` and an incident.
- Signing: an agent update or pack without a valid release signature is
  refused by a probe even when the server's install script or config supplies
  a different key or none; `deploy_agents` is refused without the local
  opt-in, in multi-tenant mode, and for a payload that fails verification.
- Isolation suite: new routes classified; results of a probe of A naming B's
  configs are dropped; `_foreign_refs` covers `integration_configs.probe_id`;
  the PostgreSQL router mode (`ROUTER_TESTS_ON_PG`) refuses a cross-tenant
  `probe_id` through the composite key.
- Job isolation: `run_integration_checks` with probe-assigned configs in both
  tenants collects none of them in the core.
- Agent (Rust): supervisor restart and backoff, buffer bounds (count and bytes,
  latest wins), signature rejection, secret redaction in logs, the local floor.
- End to end in CI: an agent in probe mode against the backend, with a fixture
  HTTP server standing in for Proxmox.

## Risks

- **Footprint pushback.** A runtime of tens of MB on a customer box.
  Mitigation: downloaded only when integrations are assigned; a probe that only
  checks hosts is unchanged; the native-primitive rule keeps SNMP and checks
  out of Python.
- **Runtime supply chain.** CPython and httpx become something shipped to
  customers. Mitigation: pinned and hashed dependencies, built in CI, signed
  with the release key; security updates ride the normal release.
- **Drift between core-run and probe-run collections.** Same code, but a
  different interpreter or dependency set could behave differently.
  Mitigation: the pack's versions equal the backend image's; contract tests run
  against both.
- **Hooks that quietly need the network.** A future `on_snapshot` that calls
  out would run in the SaaS and fail there. Mitigation: the boundary test covers
  hook modules.
- **Version skew in the SaaS.** The backend updates often, probes lazily.
  Mitigation: the schema window, the pack fetched by hash from the heartbeat,
  "needs update" raised as an incident.

## Open questions for the owner

1. **Speedtest licence.** Whether the Ookla CLI's licence allows running it on
   customer probes as part of a commercial service has to be checked.
   Alternatives: speedtest-cli (unmaintained, see `speedtest.py`), LibreSpeed
   CLI, or measuring against our own endpoints.
2. **ARM probes** (Raspberry Pi): the agent ships x86_64 builds only today. Are
   they needed at launch? They double the runtime builds.
3. **Credential default in the SaaS**: is S2 (sealed — the SaaS cannot read
   integration secrets) the launch default, or a P4 upgrade after a launch with
   S1?
4. **Probe-local secret cache** (S1): on by default (survives SaaS outages) or
   off (nothing on disk at the customer)?
5. ~~Agent update key~~ — no longer open: release-key signing of agent updates
   with mandatory verification is a precondition of P2 in the SaaS (P2a);
   without it rule 4 does not hold.
