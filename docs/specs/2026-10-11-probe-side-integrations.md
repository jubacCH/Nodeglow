# Probe-side integrations

Status: design · 2026-10-11 — not implemented. Owner decision 4 of
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
  the stored `ssh_private_key` (`pct exec` into each LXC).
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
- Auto-update: the agent verifies SHA-256 and, when configured, an Ed25519
  signature. The signing key is **held by the server** (`services/agent_signing.py`,
  `DATA_DIR/agent_update_ed25519.key`); the agent gets the public key from the
  install script.

### Core-side jobs that ignore `probe_id`

Only the ping job filters with `probes.core_checked`. SNMP polls
(`services/snmp.py` shells out to net-snmp `snmpget`/`snmpwalk` with the
community string on the command line), SSL expiry (`update_ssl_expiry`), port
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
| shaping into `data` exactly as today | SNMP GET/WALK v2c/v3 (new; the backend keeps MIB/OID logic) |
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
  and no longer puts the community string on a command line, where every local
  user can read it.
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

## Recommendation

**Option D.** API collectors run as today's Python code in a supervised,
signed collector runtime on the probe; host-level primitives (existing checks,
SNMP, TLS expiry, port probe, DNS, speedtest) are native in the agent. The
probe owns the schedule for what it is assigned and delivers finished
`CollectorResult`s; the backend keeps alerts, hooks, storage and UI, through
**one** write path shared with core-run collections.

Design rules that follow:

1. A config is collected by exactly one party: the core (`probe_id IS NULL`)
   or its probe. The core never collects a probe-assigned config, and never
   fills in for a silent probe.
2. No backend code path needs a decrypted integration secret for a
   probe-assigned config (so credentials can be sealed to the probe later).
3. The probe returns shaped data of known collectors, never raw responses to
   backend-chosen requests.
4. Code that runs on the probe is signed by a key the SaaS server does not hold.
5. Results carry the time they were collected, not the time they arrived.

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
integration_configs.probe_id    INT NULL, tenant_fk -> agents ON DELETE SET NULL
integration_configs.config_rev  INT NOT NULL DEFAULT 1   -- bumped on every config change
snapshots.collected_by          INT NULL                 -- probe id, NULL = core
snapshots.collected_at          DATETIME NULL            -- probe clock, corrected
```

`probe_id` is a composite tenant FK like `ping_hosts.probe_id`, so a config can
never point at another tenant's probe (RLS and the composite key both refuse
it); the config's site follows from the probe. Both tables are already
`TenantScoped` with RLS and need no new policy, but `migrate.py`'s
re-application of the RLS classification and the classification test must
still pass, and the isolation walk's `_foreign_refs` must learn
`integration_configs.probe_id`.

`snapshots.timestamp` stays the authoritative "as of" time and is set from
`collected_at`; `collected_by` and the arrival lag go to the status API.

No table for commands in the first cut (see "Command channel").

### Wire contract

Probe and backend stay on the agent token; there is no new credential. New
endpoints live under `/api/agent/` (already in `_AUTH_SKIP_PREFIXES`,
authenticated by their own token), run in the probe's `tenant_scope`, and are
listed in `TENANTLESS_ROUTES` of `tests/test_routers/test_tenant_isolation.py`
with that reason — plus isolation tests of their own (a probe of tenant A
naming a config of tenant B is dropped, as `_record_probe_results` does for
hosts).

**Heartbeat** (existing): the response gains a small revision marker, so the
heartbeat stays cheap and never carries secrets:

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
 "native": {"snmp": […], "tls_expiry": […], "speedtest": {"interval_seconds": 3600}}}
```

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
- A `config_rev` older than the stored one is rejected: data collected with an
  old host or old credentials must not overwrite the new config's state.
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

**Native primitives** use the existing heartbeat pattern of `checks` /
`check_results` with their own result types (values per OID, TLS expiry days,
open ports), because they are small and periodic.

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

- `run_integration_checks` skips probe-assigned configs (the `core_checked`
  equivalent for configs) — rule 1.
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
  `collect`, `test`, `refresh_assignments`, `deploy_agents`, `wipe`. The
  result of `collect`/`test` comes back through the results endpoint with the
  command id; the UI request waits up to its timeout, then shows "pending".
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
| SaaS can read it | yes (with the tenant key) | **no** | no |
| Probe at rest | encrypted with a probe-local key, or memory only | sealed blob + probe private key | the customer's file |
| Crypto-shred on purge (decision 7) | destroying the tenant key covers the SaaS copy; the probe gets `wipe` | inherent; the probe gets `wipe` | nothing in the SaaS |
| Support access (decision 8) | an approved operator session could in principle reach plaintext | cannot reveal secrets even with approval | cannot |
| Replacing the probe | transparent | secrets re-entered (or sealed to every probe of the site up front) | re-create the local file |
| Effort | S | M (X25519 sealed boxes in browser and agent, re-seal flow) | S |

Recommendation: **S1 first (P2), S2 as the SaaS default (P4), S3 always
available** — the answer for customers who want no credential in the SaaS at
all.

Interaction with per-tenant envelope keys (decision 7, built in parallel):

- Probe-assigned configs are encrypted exactly like core-run ones. This design
  calls whatever replaces `services/integration.py:decrypt_config` with the
  tenant's data key and must not keep a path that uses `SECRET_KEY` directly.
  The assignments endpoint decrypts in the probe's tenant scope — the same
  place the tenant key is unwrapped for core collections.
- Under S2 the sealed blob sits inside the DEK-encrypted `config_json`. Purge
  destroys the DEK; the SaaS copy is gone either way.
- Purge (the phase 9 lifecycle) also queues `wipe` for every probe of the
  tenant: the agent deletes assignments, cached secrets, its probe key and
  buffered results, then uninstalls (the existing `uninstall` path). A probe
  that never comes back keeps only data that was the customer's to begin with;
  the purge report lists it.
- Probe-local persistence: S1 secrets may be cached on the probe, so a probe
  that restarts during a SaaS outage keeps collecting — encrypted with a
  probe-local key (DPAPI on Windows, a 0600 key file on Linux). Proposed
  default: on, because surviving an outage is the point of collecting locally
  (open question 4).
- The probe's own keys: generated at enrolment (X25519 for sealing; the public
  key goes to the backend with the enrol request). Rotation = new key +
  re-seal.

The Proxmox **deploy-agent** feature is the one place where the backend uses a
secret itself (SSH from the backend). For probe-assigned Proxmox configs it
becomes the probe command `deploy_agents`, executed by the probe, or is
unavailable — rule 2.

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
| Probe silent (offline, crashed, deleted) | its integrations `unknown`, with "last collected by probe X at T"; one incident per probe | per-probe freshness extended to integrations: `problems_for` counts hosts **and** configs; a config is `unknown` when its probe is stale or its newest snapshot is older than `interval × 3 + heartbeat` |
| Target unreachable from the probe | integration error, as today | `success=false` with the collector's error |
| Collector runtime crash-looping or missing | configs `unknown` ("collector unavailable on probe X"), incident | runtime state in the heartbeat; never reported as a target failure |
| SaaS unreachable | nothing until it is back, then the backlog oldest first | probe buffer, latest wins |
| Duplicate delivery | nothing | result `id` |
| Config edited while a result is in flight | the old result is discarded | `config_rev` |
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
  private key, so even a compromised SaaS cannot make probes run code it wrote
  (rule 4). For self-built on-prem installs (git mode) the operator can set a
  pack key in the agent config, delivered by the install script like
  `update_public_key`. The agent's own update key is server-held today
  (`services/agent_signing.py`); for the SaaS it should move to a release key
  too — an open question, not part of this work.
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

- Nothing changes on upgrade: `probe_id` is NULL for every existing config, the
  core keeps collecting it, and single-tenant on-prem installs never have to
  touch probes.
- "Move to probe" on a config: choose a probe → the backend sends a `test`
  command → on success `probe_id` is set and `config_rev` bumped. Snapshots
  keep their `entity_id`, so history, alerts and backup jobs continue. Moving
  back to the server is the same in reverse.
- Bulk: "move all integrations of site X to probe Y".
- **SaaS readiness report** (before tenancy phase 14 turns on the egress
  restriction for a tenant): every config whose target is private and that has
  no probe, every private host the core checks, every SNMP / SSL / port job that
  would run from the core. Instances moving into the SaaS by export/import
  (tenancy phase 12) get the same report on import; their configs arrive with
  `probe_id = NULL` and are flagged "needs a probe" until assigned.
- `speedtest` (`network_scope = "site"`) in multi-tenant mode: on a probe only.
- `cloudflare` (`"cloud"`) stays in the core everywhere.

## Effort per integration

Under the recommendation, a Python integration needs no port. The work is a
fixture-based test that its `collect()` runs inside the pack, a check that it
does not reach into the backend, and whatever it does today outside `collect()`.
Option B is listed for comparison.

| Integration (`name`) | Transport / auth | Scope | Work beyond the generic path | D | B |
|---|---|---|---|---|---|
| Proxmox VE (`proxmox`) | HTTPS, API token | any | deploy-agent via SSH → probe command; backup sync and bandwidth already work from data | S + M | L |
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
| SNMP polls | core, net-snmp CLI with the community on the command line | Rust SNMP v2c/v3 GET/WALK; the backend sends `(host, port, credential, oids)`; M |
| SSL expiry | core, for every host | TLS handshake on the probe for its hosts; S |
| Port discovery | core, for every host | bounded TCP connect scan on the probe; S |
| DNS resolution of hosts | core | resolve on the probe (the customer's DNS); S |
| Subnet scans | core | separate change (non-goal here); M |

## Phased plan

| # | Step | Where | Effort |
|---|---|---|---|
| P0 | Spike: build a pack (python-build-standalone + httpx + integrations) for linux-x86_64 and windows-x86_64; measure size and RSS; run Proxmox/UniFi/Synology `collect()` from it against recorded fixtures; check the speedtest licence | — | S |
| P1 | Collector split (backend only, no behaviour change): `_base` without FastAPI, boundary test, shared `integration_ingest` write path, `network_scope`, phpIPAM/UniFi splits; migration for `probe_id`, `config_rev`, `collected_by`, `collected_at`; the core skips probe-assigned configs | core | M |
| P2 | Probe runtime: download + signature check (release key), supervisor, local scheduler, latest-wins buffer, assignments and results endpoints, S1 secrets, S3 local references, per-probe freshness and `unknown` for integrations, runtime state in the heartbeat; pilot Proxmox, UniFi, Synology | core (agent + backend) | L |
| P3 | Long-poll command channel; UI: "Collected by", move to probe, test via probe, status and lag; the remaining HTTP integrations and NUT (a fixture test each); auto-imported hosts follow the probe; readiness report | core + frontend | M |
| P4 | Sealed credentials (S2): probe key at enrolment, browser sealing, re-seal; signed result batches; `wipe` hooked into tenant purge (phase 9 lifecycle) | core (+ ee purge hook) | M |
| P5 | Native primitives: SNMP, SSL expiry, port discovery, DNS on the probe; speedtest as a site job; Proxmox deploy-agent via the probe | core (agent + backend) | M + M |
| P6 | SaaS enforcement (with tenancy phase 14): the core refuses private targets in multi-tenant mode; per-tenant ingestion quotas; plan-tier interval floors | core + ee | S |

S ≈ days, M ≈ 1–2 weeks, L ≈ 3–5 weeks for one developer. P0–P3 (a SaaS
customer's Proxmox, UniFi, NAS and HTTP integrations working through a probe)
is about two months; P0–P6 about three and a half.

Ordering with the tenancy phases: P1 can start now (backend only, independent
of phase 9). P2 touches the agent's main loop, as phase 10's syslog relay does;
both add a probe capability next to `checks`, so they should agree on one shape
(e.g. `capabilities: ["checks", "syslog", "integrations:1"]` in the heartbeat)
before either merges. P4 depends on the envelope-key work and the phase 9
purge. P6 is part of phase 14.

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
- Ingest tests: idempotency, `config_rev`, `not_assigned` (including another
  tenant's config id), clock correction, size limits, the cluster-group
  freshness rule with interleaved arrivals, `unknown` while the probe is stale.
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
5. **Agent update key**: move the agent's self-update signing to a release key
   for the SaaS (the same reasoning as rule 4)? A separate change, but the same
   release pipeline.
