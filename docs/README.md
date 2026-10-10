# Nodeglow documentation

Start with the [README](../README.md) for what Nodeglow is and how to get it
running. This page lists every document in the repository, by reader, and
keeps the **current** reference apart from **historical** design records.

## Current

### Operators — installing and running Nodeglow

| Document | Read it for |
|---|---|
| [INSTALL.md](INSTALL.md) | Installing a **release** (signed GHCR images, `install.sh`, offline bundle), editions, upgrading, backup/restore, verifying signatures, moving a git install to releases |
| [OPERATIONS.md](OPERATIONS.md) | Day-2 operation for **both** kinds of installation: sizing, ports, hardening, updates (incl. git mode), backups, the encryption key, enterprise license, AI settings, troubleshooting |
| [OPERATIONS.md → Configuration reference](OPERATIONS.md#configuration-reference) | **The** list of environment variables (`.env`) — other documents link here |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed per release |

Two installation types, one set of docs:

| | Release install | Git checkout |
|---|---|---|
| Who | Customers, on-prem, air-gapped | Development, the project's own production |
| Made with | `install.sh` → `docker-compose.release.yml` | `git clone` → `docker-compose.yml` |
| Updater mode | `NODEGLOW_UPDATE_MODE=image` — newest signed release | `NODEGLOW_UPDATE_MODE=git` — `origin/main`, built on the host |
| Install / upgrade | [INSTALL.md](INSTALL.md) | [README → Run from source](../README.md#run-from-source), [OPERATIONS.md → Updating](OPERATIONS.md#updating) |

### Integrators — using the API

| Document | Read it for |
|---|---|
| [API.md](API.md) | Authentication (API keys, sessions, CSRF), `/api/v1` and `/api/v2` endpoints, host state, incident filters and envelope, bulk host edit |
| [design/05-dashboard-api.md](design/05-dashboard-api.md) | Field-level description of `/api/v2/dashboard`, `/summary` and the change feed (German) |

### Contributors — working on the code

| Document | Read it for |
|---|---|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Workflow, CLA and DCO sign-off, tests and checks, commit style |
| [../frontend/README.md](../frontend/README.md) | Frontend development: dev server, tokens, navigation registry, tests |
| [design/04-design-system.md](design/04-design-system.md) | **The** design system: tokens, components, glow and state rules, app shell (German) |
| [design/02-information-architecture.md](design/02-information-architecture.md) | Navigation, object model and workflows behind the UI (German); `frontend/src/lib/navigation.ts` is the source of truth for sections and routes |
| [../ee/README.md](../ee/README.md) | The open-core boundary, the plugin loader, extension points, license keys, adding an enterprise feature |

### Licensing

| Document | |
|---|---|
| [../LICENSING.md](../LICENSING.md) | Overview: AGPL core, `ee/` under the Nodeglow Enterprise License, images, commercial licensing |
| [../LICENSE](../LICENSE) / [../ee/LICENSE](../ee/LICENSE) | License texts (the enterprise license is a draft pending legal review) |
| [../CLA.md](../CLA.md) | Contributor License Agreement (draft) |
| [../NOTICE](../NOTICE), [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) | Copyright notice, third-party licenses |

## In progress

| Document | Status |
|---|---|
| [specs/2026-10-10-multi-tenancy-design.md](specs/2026-10-10-multi-tenancy-design.md) | Multi-tenancy (MSP) design; implementation of the first phases is in progress. Nothing tenant-related is released yet. |

## Historical

Kept as records of how decisions were made. They are **not** maintained;
where they disagree with the documents above or with the code, the code and
the current documents win.

| Document | Status |
|---|---|
| [specs/2026-06-10-update-orchestrator-design.md](specs/2026-06-10-update-orchestrator-design.md) | Implemented (git-mode updater). Image mode for releases came later — see [INSTALL.md](INSTALL.md#upgrading) |
| [specs/2026-08-28-remote-probes-design.md](specs/2026-08-28-remote-probes-design.md) | Implemented (remote probes, probe-aware host state) |
| [design/README.md](design/README.md) | Index of the 2026 redesign records (German) |
| [design/01-ux-audit.md](design/01-ux-audit.md) | UX audit of the pre-redesign UI (German) |
| [design/03-design-directions.md](design/03-design-directions.md), [design/directions/](design/directions/), [design/prototypes/](design/prototypes/) | Design directions A–E3 and their prototypes; E3 was chosen and implemented (German, simulated data) |
| [../DESIGN_GUIDE.md](../DESIGN_GUIDE.md) | Superseded "Command Center" theme of the old UI — replaced by [design/04-design-system.md](design/04-design-system.md) |
| [FRONTEND_SPEC.md](FRONTEND_SPEC.md) | Superseded spec of the first Next.js frontend |
