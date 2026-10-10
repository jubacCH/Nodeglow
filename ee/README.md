# Nodeglow Enterprise (`ee/`)

This directory holds Nodeglow's commercial features. Its contents are licensed
under the [Nodeglow Enterprise License](LICENSE) (draft, pending legal review),
**not** under the AGPL-3.0 that covers the rest of the repository. See
[`../LICENSING.md`](../LICENSING.md) for the overview.

In short: the code is visible, you may modify it for your own use and run it
for development and testing; production use needs a valid subscription /
license key; redistribution is not permitted.

## What is here

| Feature | Package | Hooks into the core via |
|---|---|---|
| **HA: scheduler leader election** — with `REDIS_URL` set, all backend processes contend for one Redis lease; only the holder runs scheduled jobs | `nodeglow_ee.ha` | scheduler coordinator |
| **Glow** — the AI assistant chat, `POST /api/v1/glow/chat` | `nodeglow_ee.ai.glow` | router |
| **AI postmortems** — drafted when an incident is resolved (manually or by correlation), regenerated via `POST /api/v1/incidents/{id}/postmortem` | `nodeglow_ee.ai.postmortem`, `.postmortem_api` | incident-resolved hook, router |
| **AI daily summary** — daily job and `POST /settings/ai/test-summary` | `nodeglow_ee.ai.daily_summary` | scheduler hook, router |

Planned: multi-tenancy and the MSP portal, SSO (SAML / OIDC) + SCIM, custom
RBAC, on-call escalation, SLA reports per customer, audit export / SIEM and
long audit retention.

Everything else — including the building blocks these features use (data
model, collectors, alerting, the extension points themselves) — belongs in the
AGPL core. If a change is useful to every self-hoster, it goes into the core.
These stay in the core on purpose:

- **AI plumbing**: provider abstraction (`services/ai_client.py`), opt-in and
  provider settings (`services/ai_config.py`, `routers/settings/ai.py`),
  redaction (`services/ai_redaction.py`), the token-usage log and
  `/api/v1/ai/status`. The daily-summary *settings* keys are stored by the
  core settings endpoints as well, so the settings tab keeps one save path.
- **The Redis rate limiter** (`ratelimit.py` on `services/shared_state.py`): a
  security control, not a scale-out feature, and fully separable from the
  leader lock. The lock moved here and reuses the core's Redis client through
  `shared_state.redis_client()`.
- **The frontend.** All UI code stays in `frontend/` under the AGPL. Splitting
  React components across licences and build trees would cost more than it
  protects: the value is the backend logic, and a UI without its endpoints
  does nothing. The UI asks `GET /api/v2/features` and renders enterprise
  pieces only when their flag is true; where a user would look for them
  (Settings → AI, the postmortem card of an incident) it shows one calm
  sentence instead.

## Structure

```
ee/
├── LICENSE                  Nodeglow Enterprise License (draft)
├── README.md                this file
├── .dockerignore            keeps tests and caches out of the image
└── backend/
    ├── pyproject.toml       package "nodeglow-ee"; entry point nodeglow.plugins → nodeglow_ee:plugin
    ├── nodeglow_ee/
    │   ├── __init__.py      EnterprisePlugin.register(registry), the single entry point
    │   ├── licensing.py     license format, offline verification, keygen/issue/verify CLI
    │   ├── license_runtime.py  license source + cache, feature gates, /settings/license
    │   ├── ha/              leader_lock.py (Redis / in-memory lease), coordinator.py
    │   └── ai/              glow.py, postmortem.py, postmortem_api.py, daily_summary.py,
    │                        context.py (prompt context), common.py
    └── tests/               collected through backend/pytest.ini (testpaths)
```

`nodeglow_ee` imports core modules (`services.*`, `routers.*`, `models.*`,
`extensions`) the way the core imports them: it runs inside the backend
process with `backend/` on `sys.path`.

## The boundary

The rule is one-way: **`ee/` may depend on the core; the core never depends on
`ee/`.**

- The core must build, start and pass its tests with `ee/` deleted or
  disabled (`NODEGLOW_DISABLE_EE=1`). CI runs the backend suite both ways,
  and `backend/tests/test_ee_boundary.py` fails if any core module imports
  `nodeglow_ee`.
- The only core module that knows about `ee/` is the loader
  (`backend/ee_loader.py`), and it treats "nothing found" as normal.
- Core code that `ee/` hooks into exposes a documented extension point in
  `backend/extensions.py`; `ee/` never monkey-patches core internals.
- Database tables owned by `ee/` get their own migrations under `ee/`, so a
  core-only installation never creates them. (The features here today need
  no tables of their own: postmortems use the core `incidents.postmortem`
  column, usage goes to the core `ai_usage_log`.)
- Copying code from `ee/` into the core relicenses it under the AGPL, so it
  needs the owner's decision; copying core code into `ee/` is fine (it stays
  available under the AGPL in the core).

## How the core finds `ee/` (`backend/ee_loader.py`)

When `main.py` is imported it calls `ee_loader.load_plugins()` and then mounts
the routers the plugins registered, after the core routers. Discovery:

1. `NODEGLOW_DISABLE_EE=1` (or `true` / `yes` / `on`): nothing is loaded —
   community edition, even with `ee/` present.
2. Python entry points in the group `nodeglow.plugins` (an installed
   package, e.g. `pip install ./ee/backend`).
3. The source tree: `NODEGLOW_EE_PATH` if set, else `../ee/backend` next to
   `backend/`. If it contains `nodeglow_ee/`, that directory goes on
   `sys.path` and `nodeglow_ee.plugin` is registered.

A plugin that fails to import or register is logged and skipped; the core
always starts. The startup log line `Nodeglow edition: community|enterprise`
says which one is running.

**Docker.** `docker-compose.yml` passes `./ee` as the named build context `ee`
(`additional_contexts`, Docker Compose 2.17 or newer). The Dockerfile copies
`ee/backend` to `/opt/nodeglow-ee` and sets `NODEGLOW_EE_PATH`, so the image
built by `docker compose build` or the update sidecar contains every feature.
A plain `docker build backend/` (no `ee` context) falls back to an empty stage
and produces a community image.

## Extension points (`backend/extensions.py`)

`register(registry)` receives `extensions.registry` and may call:

| Call | Effect in the core |
|---|---|
| `add_router(router)` | `app.include_router(router)` after the core routers |
| `add_scheduler_hook(async fn(scheduler))` | awaited in `start_scheduler()` to add jobs |
| `set_scheduler_coordinator(obj)` | if `obj.wants_control()`, `await obj.start(scheduler)` replaces `scheduler.start()`; `obj.stop()` runs on shutdown |
| `on_incident_resolved(async fn(incident_id))` | spawned after a manual or automatic resolve has committed |
| `enable_feature(name)` | flag reported by `GET /api/v2/features` |
| `set_license_provider(async fn())` | gates the reported flags; returns `{"license": summary, "active": {name: bool}}` |

`GET /api/v2/features` (session or API key) returns
`{"edition": "community" | "enterprise", "features": {"ha_scheduler": bool,
"ai_assistant": bool, "ai_postmortem": bool, "ai_daily_summary": bool},
"installed": {...}, "license": {...} | null}`. A flag in `features` means
*usable*: installed and licensed. `installed` is the same map without the
license check; `license` is the summary (status, message, expiry) and `null`
in the community edition. Whether a feature is switched on (e.g. the AI
opt-in) is reported by its own status endpoint.

## Adding an enterprise feature

1. If the core has no extension point where the feature plugs in, add one to
   `backend/extensions.py` first (AGPL, generic, a no-op without plugins) and
   call it from the core. Add the feature's community default to
   `DEFAULT_FEATURES` if the UI needs a flag.
2. Put the logic in a subpackage of `nodeglow_ee` with a
   `register(registry)` function and call it from `EnterprisePlugin.register`
   in `nodeglow_ee/__init__.py`. Add its flag name to
   `licensing.KNOWN_FEATURES` and gate it: endpoints start with
   `if refused := await license_runtime.blocked("<flag>"): return refused`,
   jobs and hooks with `if not await license_runtime.is_active("<flag>"): return`.
3. Tests go in `ee/backend/tests/`; they may use the core fixtures (`db`,
   `tests.test_routers.conftest.make_client`).
4. UI: build it in `frontend/` and gate it with `useFeatures()` /
   `hasFeature(...)` (`frontend/src/lib/features.ts`).
5. Run the backend suite twice: `python -m pytest -q` and
   `NODEGLOW_DISABLE_EE=1 python -m pytest -q`.

## License keys

Production use of `ee/` needs a license key. Operators: see
[`docs/OPERATIONS.md` → Enterprise license](../docs/OPERATIONS.md#enterprise-license).
The terms in [`LICENSE`](LICENSE) apply regardless of enforcement.

**Format** (`nodeglow_ee/licensing.py`). A JSON envelope
`{"format": "nodeglow-license/1", "kid", "payload", "signature"}`, usually
passed around base64 encoded on one line. `payload` is the base64 of the
license JSON: `license_id`, `customer`, `edition` (`"enterprise"`),
`features` (flag names, `"*"` = all, including future ones), `max_tenants`
(`null` = unlimited), `issued_at`, `expires_at`, optional `install_id`. The
Ed25519 signature covers `"nodeglow-license/1\n" + kid + "\n" + payload
bytes`, so neither the key id nor a byte of the payload can change. It is
verified offline (same primitive as agent update signing,
`services/agent_signing.py`) against `TRUSTED_KEYS`, a map of key id →
public key embedded in the package. Rotation = add a key id, retire the old
one later. There is deliberately no way to trust another key at runtime.

**Source** (`nodeglow_ee/license_runtime.py`). `NODEGLOW_LICENSE` (the key or
a file path) wins; otherwise the `ee_license` row of the `settings` table,
written by `POST /settings/license` (admin only, audit-logged as
`license.install`; `DELETE` → `license.remove`; `GET` shows the details and
the installation ID). Each process caches it for 60 s, so an upload reaches
all workers within a minute, without a restart.

**Enforcement.** The plugin always registers — routers, hooks, coordinator —
and every feature asks the license when it is used:

| State | Features | `/api/v2/features` |
|---|---|---|
| `missing`, `invalid` | inactive: endpoints `402` (`code: license_missing` / `license_invalid`), jobs and hooks skip, HA runs single-instance | every flag `false`, `installed` `true` |
| `valid` | per the license's feature list (`402 feature_not_licensed` otherwise) | licensed flags `true` |
| `grace` (14 days after `expires_at`) | still working, admin banner | as `valid` |
| `expired` (after grace) | refused (`402 license_expired`); data already produced stays readable through the core (stored postmortems); HA leader election keeps running (`EXPIRY_EXEMPT`) because stopping it would duplicate every job | AI flags `false`, `ha_scheduler` `true` |

Monitoring is never gated. HA is decided once, when the scheduler starts: the
license hook (`on_scheduler_start`) runs first and the coordinator's
`wants_control()` reads its result.

**Issuing** (vendor side, also for the owner's own installations — there is
no bypass):

```bash
cd ee/backend
python -m nodeglow_ee.licensing keygen --out-dir ~/nodeglow-license-keys --kid ng-2026-10
python -m nodeglow_ee.licensing issue --signing-key ~/nodeglow-license-keys/ng-2026-10.private.pem \
  --kid ng-2026-10 --customer "ACME AG" --expires 2027-12-31 --features all [--max-tenants 10] \
  [--install-id ngi_…] --out acme-license.txt
python -m nodeglow_ee.licensing verify acme-license.txt
```

The private key can also come from `NODEGLOW_LICENSE_SIGNING_KEY` (PEM text
or path). It never goes into the repository or onto a Nodeglow server.

**Tests** sign their licenses with a key generated per run and trust it only
in memory (`tests/ee_license_helpers.py`, autouse fixture `ee_license` in
`tests/conftest.py`).

**Agent.** The Rust agent stays entirely in the core; it has no enterprise
variant.

## Contributing

Contributions to `ee/` are welcome under the same [CLA](../CLA.md) as the core.
See [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
