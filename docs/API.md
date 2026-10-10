# Nodeglow REST API

The UI and external tools talk to the same backend. This page describes the
**public API** — `/api/v1` (stable, API-key access) and `/api/v2` (read
models for the UI). Other routes the frontend proxies (`/hosts/api/*`,
`/syslog/api/*`, `/settings/*`, `/rules/*`, `/api/agents/*`, …) are internal
to the UI and may change without notice.

The source of truth is the code: `backend/routers/api_v1.py`,
`backend/routers/api_v2.py`, `backend/routers/maintenance.py`. With
`DEBUG=1` the backend also serves the generated OpenAPI docs at `/api/docs`
(off in production; see [OPERATIONS.md](OPERATIONS.md#hardening-checklist)).

- [Authentication](#authentication)
- [Conventions](#conventions)
- [Host state](#host-state)
- [API v1](#api-v1)
- [API v2](#api-v2)

---

## Authentication

| Caller | How | Notes |
|---|---|---|
| Scripts, integrations | `X-API-Key: ng_…` header | Keys are created by an admin (Settings → API, or `POST /api/v1/keys`) with role `readonly`, `editor` or `admin`. The key is shown once. Keys in the query string are rejected with `400`. |
| The UI (browser) | Session cookie | Mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) must also send the CSRF token: the value of the `ng_csrf` cookie in an `X-CSRF-Token` header (double-submit). This applies to `/api/v1` too whenever a session cookie is present. |

A request with `X-API-Key` is authenticated by the key only; the session
cookie is ignored for it, which is why it needs no CSRF token.

Roles: `readonly` may read; `editor` may also create, change and delete
hosts, acknowledge/resolve incidents, edit agents and maintenance windows;
`admin` additionally manages API keys, reads the audit log and handles
backups. A request
without valid credentials gets `401`, a missing role `403`.

`/api/v2` currently accepts **session callers only** — the auth middleware
admits API keys under `/api/v1` and nowhere else. The v2 route dependencies
already understand API keys; opening v2 to them is a middleware change.

## Conventions

- Timestamps in responses are UTC ISO-8601. Fields produced by the newer
  services (host state, v2) carry a `Z` suffix; older v1 fields are naive
  ISO strings that are also UTC.
- Query parameters that take a date accept ISO-8601 with or without offset
  (`2026-10-10T08:00:00Z`); an unparsable value is a `400`.
- Comma-separated filters (`state=down,warning`) are validated: an unknown
  value is a `400` that lists the allowed ones.
- Errors are `{"detail": "…"}` (FastAPI) or, from the middleware,
  `{"error": "…"}`.
- Enterprise endpoints answer `402` with a `code` (`license_missing`,
  `license_invalid`, `feature_not_licensed`, `license_expired`) when the
  license does not cover them, and do not exist in the community edition.
  AI endpoints answer `409 {"code": "ai_disabled"}` until an admin opts in.

## Host state

Every host endpoint adds the unified, probe-aware state
(`backend/services/host_state.py`), so a list, a badge and the dashboard can
never disagree:

| Field | Meaning |
|---|---|
| `state` | `up`, `degraded`, `warning`, `down`, `unknown`, `maintenance` or `disabled` |
| `state_reason` | Short sentence for a tooltip, e.g. why a host is `unknown` |
| `observed_at` | Time of the newest real check, `null` if there is none |
| `status` | Legacy vocabulary derived from `state`: `up`/`degraded`/`warning` → `online`, `down` → `offline`, otherwise the state itself |

Rules, first match wins: `disabled` (monitoring off) → `maintenance` (flag or
active window) → `unknown` (silent probe, no result, or the newest result is
older than 3× the check interval, at least 180 s) → `down` (newest check
failed) → `warning` (a service check fails: port/HTTP check errors or an open
service incident) → `degraded` (latency above threshold, or named in another
open incident) → `up`.

`status` used to read `online` for a host whose probe had gone silent; it now
reads `unknown`, like `state`.

---

## API v1

Base path `/api/v1`. Unless noted, reading needs any role and changing needs
`editor`.

### System and keys

| Method | Path | |
|---|---|---|
| GET | `/status` | Counts of hosts, agents, integrations, open incidents |
| GET / POST | `/keys` | List / create API keys — admin. Body `{name, role, note}` |
| DELETE | `/keys/{id}` | Delete an API key — admin |
| GET | `/audit` | Audit log — admin |
| GET | `/ai/status` | Whether AI features are enabled |
| GET | `/predictor/eval` | Quality metrics of the incident predictor |

### Hosts

| Method | Path | |
|---|---|---|
| GET | `/hosts` | All hosts with [state](#host-state), latency, last check, uptime (`h24`, `d7`, `d30`), maintenance fields, `probe_id`. Filters: `state` (comma list), `status` (legacy), `source` (`manual`, `proxmox`, `agent`, `phpipam`), `enabled` |
| GET | `/hosts/{id}` | Detail with latest metrics (agent metrics for agent hosts, matching integration data) |
| GET | `/hosts/{id}/history` | Ping history |
| GET | `/hosts/{id}/timeline` | Merged timeline: incidents, changes, syslog — each source gets a fair share of the limit |
| POST | `/hosts` | Create. `name`, `hostname` required; `check_type` (`icmp`, `tcp`, `http`, `https`), `port`, `latency_threshold_ms`, `enabled`, `http_options`. The hostname goes through the same SSRF validation as the UI |
| PATCH | `/hosts/{id}` | Update any of `name`, `hostname`, `check_type`, `port`, `latency_threshold_ms`, `enabled`, `maintenance`, `maintenance_until`, `http_options`, `probe_id` (`null` = checked by the server). Changed fields are audited |
| PATCH | `/hosts/bulk` | Bulk update, see below |
| DELETE | `/hosts/{id}` | Delete |
| POST | `/hosts/{id}/maintenance` | Per-host maintenance flag: `action` `toggle` (default, with optional `duration`), `schedule` (with `until`, ISO-8601) or `off` |

**`http_options`** (HTTP/HTTPS checks): method, path, expected status codes,
keyword, redirect handling. Invalid options are a `400`; see
`backend/utils/http_options.py` (or the host form) for the exact schema.

**Bulk update** — `PATCH /api/v1/hosts/bulk`:

```json
{ "ids": [12, 13, 14], "updates": { "enabled": false, "probe_id": 7 } }
```

Allowed fields: `check_type`, `enabled`, `latency_threshold_ms`,
`maintenance`, `probe_id`; others are ignored and listed. At most 5000 ids.
Response:

```json
{ "ok": true, "updated": 3, "ids": [12, 13, 14], "missing": [], "ignored_fields": [] }
```

### Maintenance windows

Base path `/api/v1/maintenance-windows`: `GET` (list), `GET /{id}`, `POST`,
`PATCH /{id}`, `DELETE /{id}`. Fields: `name`, `enabled`, `kind` (`weekly` or
`once`), `weekdays`, `start_time`, `duration_minutes` (weekly), `starts_at`,
`ends_at` (once), `timezone`, `all_hosts`, `host_ids`. Hosts inside an active
window have state `maintenance`.

### Incidents

| Method | Path | |
|---|---|---|
| GET | `/incidents` | List, see below |
| GET | `/incidents/{id}` | Detail with event timeline and related syslog analysis |
| POST | `/incidents/{id}/acknowledge` | Acknowledge |
| POST | `/incidents/{id}/resolve` | Resolve |
| POST | `/incidents/{id}/feedback` | Label as real or noise (trains the predictor) |
| POST | `/incidents/{id}/postmortem` | Regenerate the AI postmortem — enterprise, AI opt-in |

`GET /api/v1/incidents` parameters:

| Parameter | |
|---|---|
| `status` | Comma list of `open`, `acknowledged`, `resolved`, or `all` |
| `severity` | Comma list of `critical`, `warning`, `info` |
| `search` | Substring of the title |
| `rule` | Rule name |
| `host_id` | Only incidents whose recorded hosts include this host |
| `host_name` | Host name in the event summaries |
| `from`, `to` | `created_at` range (ISO-8601, `to` exclusive) |
| `sort` | `updated` (default), `created`, `severity` |
| `limit`, `offset` | Paging; `limit` 1–500, default 50 |
| `envelope` | `true` returns an envelope instead of a bare list |

Without `envelope` the response is a bare JSON array (as before) and the
total is in the `X-Total-Count` header. With `envelope=true`:

```json
{ "items": [ … ], "total": 128, "limit": 50, "offset": 0, "has_more": true }
```

Each item: `id`, `rule`, `title`, `severity`, `status`, `acknowledged`,
`summary` (the latest explaining event), `created_at`, `updated_at`,
`resolved_at`, `acknowledged_by`, and the affected hosts — `host_ids`,
`host_count`, `hosts` (`null` for incidents created before hosts were
recorded).

### Agents

| Method | Path | |
|---|---|---|
| GET | `/agents` | All agents |
| GET | `/agents/{id}` | Detail with latest metrics |
| PATCH | `/agents/{id}` | `log_levels`, `log_channels`, `log_file_paths`, `agent_log_level`, `enabled`, and probe mode: `is_probe`, `probe_interval_seconds` |
| DELETE | `/agents/{id}` | Delete / decommission |
| POST | `/agents/{id}/uninstall` | Queue a remote uninstall |
| GET / PUT | `/agents/{id}/services` | Watched services. PUT body `{"services": [...]}`; GET also returns the last reported states |

Install tokens for new agents: `POST /api/agents/install-tokens` (admin).

### Integrations, logs, topology

| Method | Path | |
|---|---|---|
| GET | `/integrations` | All integration instances with latest status |
| GET | `/integrations/{id}` | Detail with snapshot data |
| GET | `/syslog` | Query syslog messages |
| GET | `/syslog/stats` | Log overview statistics |
| GET | `/syslog/intelligence` | Patterns, noise, bursts |
| GET | `/topology` | Topology graph |

### Backup

| Method | Path | |
|---|---|---|
| GET | `/backup/info` | Table counts and database size — admin |
| POST | `/backup` | JSON export, always encrypted: body `{"passphrase": "…"}` (≥ 12 characters, scrypt + AES-256-GCM) — admin, rate-limited |
| POST | `/backup/restore` | Body `{"backup": <file>, "passphrase": "…"}`; old unencrypted exports only with `"allow_unencrypted": true` — admin |

Details: [OPERATIONS.md → The encryption key](OPERATIONS.md#escrow-it).

---

## API v2

Base path `/api/v2`, session only (see [Authentication](#authentication)).
Built on the same functions as v1 (`services/host_state.py`,
`services/incident_view.py`). Responses are `Cache-Control: no-cache`.

| Method | Path | |
|---|---|---|
| GET | `/features` | Edition and feature flags |
| GET | `/dashboard` | Everything the Overview dashboard shows, in one call |
| GET | `/summary` | Consistent counts for the navigation badges |
| GET / POST | `/me/seen` | Read / set the signed-in user's last dashboard visit |
| GET | `/changes` | Change feed |

**`GET /features`**

```json
{
  "edition": "enterprise",
  "features": { "ha_scheduler": false, "ai_assistant": true, "ai_postmortem": true, "ai_daily_summary": true },
  "installed": { "ha_scheduler": true, "ai_assistant": true, "ai_postmortem": true, "ai_daily_summary": true },
  "license": { "status": "valid", "…": "…" }
}
```

A flag in `features` means *usable* (installed and licensed); `installed`
ignores the license; `license` is `null` in the community edition. Whether AI
is switched on is reported by `/api/v1/ai/status`.

**`GET /dashboard`** — optional `since` (ISO-8601) overrides "since your last
visit". Top-level keys: `generated_at`, `previous_seen_at`, `timezone`, and
one key per card — `summary`, `health`, `internet`, `incidents`, `topology`,
`groups`, `upcoming`, `latency`, `syslog`, `availability`,
`since_last_visit` — plus `errors` and `timings_ms`. A card whose data could
not be computed is `null` and named in `errors`; the rest of the response is
still valid. Values that cannot be derived are `null`, never invented. Field
level documentation (German): [design/05-dashboard-api.md](design/05-dashboard-api.md).

**`GET /summary`** — `generated_at`, `incidents` (open counts), `hosts`
(`total`, `by_state`, `attention`), `probes` (`total`, `stale`,
`stale_unused`) and related counts for the navigation badges.

**`POST /me/seen`** returns `{"dashboard_seen_at", "previous_seen_at"}`.

**`GET /changes`**

| Parameter | |
|---|---|
| `since` | Required, ISO-8601; at most 31 days back (older is clamped) |
| `until` | Default now |
| `types` | Comma list of `incident_opened`, `incident_resolved`, `incident_acknowledged`, `host_added`, `port_discovered`, `agent_enrolled`, `agent_updated`, `maintenance_started`, `probe_silent`, `config_change` (admin only) |
| `limit`, `offset` | `limit` 1–200, default 50 |

Response: `since`, `until`, `total`, `counts` (per type), `limit`, `offset`,
`has_more`, `items` — each `{type, at, title, object: {kind, id, name},
detail}`.
