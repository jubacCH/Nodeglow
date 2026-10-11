# Changelog

All notable changes to Nodeglow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for its releases.

Each release's section becomes its GitHub Release notes. Cut a release with
`scripts/release.sh X.Y.Z` (see [docs/INSTALL.md](docs/INSTALL.md#for-maintainers-cutting-a-release)).

## [Unreleased]

### Added
- Enterprise multi-tenancy (licence feature `multi_tenancy`, `max_tenants`):
  operators create, suspend, resume and delete tenants under Administration →
  Tenants (deletion = 30 days suspended, then every PostgreSQL and ClickHouse
  row of the tenant is purged), assign retention plans (30 / 90 / 365 days),
  and see licence usage. The second tenant needs the ClickHouse tenant
  rebuild and PostgreSQL row-level security (or
  `NODEGLOW_TENANTS_WITHOUT_RLS=1`). Migration 045.
- Tenant switcher in the top bar (only with more than one tenant).
- Support access: operators see a tenant's data only while its admin has
  granted time-limited access (Settings → Support access); every access is
  in the tenant's audit log and a banner shows it to every member.

## [2.0.0] - 2026-10-11

### Added
- Multi-tenancy foundation (single tenant for now, nothing to configure):
  tenants, providers and sites; `tenant_id` on every tenant-owned table and
  every ClickHouse row; a fail-closed tenant filter on all database access;
  tenant memberships as the source of roles; per-tenant settings, caches,
  WebSocket/SSE fan-out and notifications; per-tenant fair scheduling of the
  check jobs. See docs/specs/2026-10-10-multi-tenancy-design.md.
- A gating tenant-isolation test suite that walks every API route and every
  tenant job with two tenants (SQLite and PostgreSQL with RLS on); new routes
  must be classified there.
- linux/arm64 release images are back: every image is a multi-arch manifest
  list (linux/amd64 + linux/arm64), built per platform on native GitHub
  runners instead of QEMU. Signatures and attestations cover the manifest
  list. The attached offline bundle stays amd64; build an arm64 one with
  `scripts/make-offline-bundle.sh --platform linux/arm64`.
- PostgreSQL row-level security as the second tenant-isolation layer
  (`NODEGLOW_RLS`), with a separate app database user (`POSTGRES_APP_USER`,
  `POSTGRES_APP_PASSWORD`). New installs from `install.sh` get both; existing
  installs are unchanged until switched (docs/OPERATIONS.md, "Row-level
  security"). Migrations run on the owner connection (`DATABASE_OWNER_URL`,
  alias `MIGRATION_DATABASE_URL`, default `DATABASE_URL`).

### Changed
- **Ping history retention now takes effect.** "Ping history (days)" was
  stored but ignored: ClickHouse kept every ping result for its fixed 30-day
  TTL. The nightly cleanup now deletes a tenant's ping results older than the
  configured value. Installs that set a value below 30 lose the older ping
  history at the first cleanup after the upgrade; the default (30) changes
  nothing. Values above 30 cannot be honoured (the table keeps 30 days) and
  the setting is now limited to 1-30.
- ClickHouse tables carry a tenant id (multi-tenancy). Existing rows belong to
  the default tenant; a background job rebuilds each table once so the tenant
  leads its sort key (needs free disk of about twice the table's size; the
  old copy is kept for 24 h).
- Frontend build tooling moved to Tailwind CSS 4 (CSS-first `@theme` in
  `frontend/src/app/globals.css` instead of `tailwind.config.ts`, PostCSS via
  `@tailwindcss/postcss`). The UI looks the same (pixel-compared in dark and
  light at desktop and phone width). This clears the remaining `npm audit`
  findings of the dev toolchain (braces, micromatch, postcss-selector-parser
  via Tailwind 3; braces via `@next/eslint-plugin-next`'s fast-glob, now
  overridden with tinyglobby), and CI now fails on high findings in build
  tooling too.
- If you override `DATABASE_URL` (e.g. in docker-compose.override.yml) **and**
  enable a separate app user, also set `DATABASE_OWNER_URL`; otherwise the
  migrations would target the bundled `db`. The backend logs an error when
  the two URLs name different databases.

### Fixed
- Syslog lost its host mapping and allowlist after the tenancy schema
  upgrade (2026-10-11 hotfix).
- Backup/restore routes are installation-operator only; previously any tenant
  admin could export or replace the whole installation.
- System status listed expiring certificates of other tenants; host parents,
  alert-rule sources and SNMP credentials could reference another tenant's
  objects; twelve routes accepted foreign ids.
- Backup restore on PostgreSQL (sequence reset and timestamp handling).
- PostgreSQL: backup export skipped every table after the dropped legacy
  table `ping_results`, a restore of such an export failed, and
  `/api/system/status` lost its later sections for the same reason.

## [2.0.0-rc.6] - 2026-10-11

### Changed
- Release images are published for linux/amd64 only for now; the arm64
  frontend build under QEMU exceeded the 6 h job limit. arm64 will return with
  native arm runners.

## [2.0.0-rc.5] - 2026-10-10

### Fixed
- Release workflow: pin cosign-installer v4.1.2 (the action has no `v4` tag).

## [2.0.0-rc.4] - 2026-10-10

### Fixed
- Release workflow: cosign-installer v4 (v3 cannot install cosign 3).

## [2.0.0-rc.3] - 2026-10-10

### Changed
- Releases are signed with cosign 3.1.3; `SHA256SUMS` is verified with
  `cosign verify-blob --bundle SHA256SUMS.sigstore.json` (separate `.sig`/`.pem`
  files are no longer published).

### Fixed
- Updater image: cosign 3.1.3 (grpc-go CVE-2026-33186 in cosign 2.x).

## [2.0.0-rc.2] - 2026-10-10

### Fixed
- Updater image: cosign 2.6.5, Docker CLI 29.8.1 and Compose 5.5.1 replace
  binaries built with an outdated Go toolchain (Go stdlib crypto/tls
  CVE-2025-68121, grpc-go CVE-2026-33186) that failed the release Trivy gate.

## [2.0.0-rc.1] - 2026-10-10

### Added

- **Release pipeline.** Tagged releases build signed, multi-arch images
  (`ghcr.io/jubacch/nodeglow-backend`, `-backend-community`, `-frontend`,
  `-updater`) with SBOM and provenance, a Trivy gate on critical findings, and
  a GitHub Release with agent binaries, `install.sh`, `docker-compose.release.yml`,
  signed checksums and an offline bundle for air-gapped installations.
- **Installer.** `install.sh` installs or upgrades a release installation
  (online or `--offline BUNDLE`), generates `.env` with fresh secrets and
  verifies checksums and, with cosign installed, signatures.
- **Image-based updates.** With `NODEGLOW_UPDATE_MODE=image` the updater
  installs published releases: cosign verification (fail closed), pre-update
  dump, pull, migrate, restart. Git-based installs keep building from source.
- **Enterprise license keys.** Offline-verified Ed25519 license keys for the
  `ee/` features (no phone-home), installed under Settings → License or via
  `NODEGLOW_LICENSE`, with a 14-day grace period and an admin banner. Without
  a key the enterprise features stay inactive and the core works unchanged.
  `GET /api/v2/features` reports edition, installed and licensed features.
- **E3 redesign.** New design system with light, dark and system theme, app
  shell (icon rail, top bar, section sub-navigation, mobile tab bar, command
  palette) and every page migrated to it: hosts, incidents, alert rules,
  maintenance, logs, integrations, agents, topology, settings and more. Some
  pages were renamed (Logs, Log overview, Log patterns, Traffic, Weekly
  report, Discovery, Agents & Probes).
- **Dashboard v2.** The Overview route shows the new E3 dashboard on
  `/api/v2/dashboard` and `/api/v2/summary`, plus a change feed (`/changes`,
  `/api/v2/changes`) and the last dashboard visit per user
  (`/api/v2/me/seen`).
- **Remote probes:** agents can run the checks for hosts the server cannot
  reach; hosts behind a silent probe are `unknown`.
- **Incident API:** filters for status, severity, rule, host, time range and
  sort order, paging with an optional `{items, total, limit, offset, has_more}`
  envelope (`envelope=true`; the bare list stays the default, total in
  `X-Total-Count`), and the affected hosts of each incident (`host_ids`,
  `hosts`).
- **Bulk host edit** (`PATCH /api/v1/hosts/bulk`) and the probe toggle on the
  agents page now actually work and persist.
- The syslog live tail filters "severity N or worse", like the list; the SNMP
  page shows port, credential and last poll state.
- **Notification channels:** Microsoft Teams, Slack and ntfy, with settings,
  test button and rule-editor support.
- **Maintenance windows**, one-off and recurring, with a badge on affected
  hosts.
- **HTTP(S) checks** with status, keyword, method, path and redirect options,
  and recorded failure reasons.
- **Agent watched services:** the agent reports the state of configured
  services on every heartbeat; failures raise incidents. Editable on the agent
  detail page.
- **AI features are opt-in**, with a choice of provider, redaction of personal
  data and secrets before anything reaches a provider, and a settings tab with
  connection test.
- Daily scheduled Postgres backups and a key fingerprint for escrowing
  `SECRET_KEY`; structured logging with request IDs.

### Changed

- **Open core.** The core is licensed under AGPL-3.0-only; high availability
  and the AI features (Glow, postmortems, daily summary) moved to `ee/` under
  the Nodeglow Enterprise License, loaded as a plugin. The UI shows enterprise
  features only where they are installed. See [LICENSING.md](LICENSING.md).
- **Unified host state:** one probe-aware host state (`up`, `degraded`,
  `warning`, `down`, `unknown`, `maintenance`, `disabled`) for lists,
  dashboard, badges and topology, exposed as `state`, `state_reason` and
  `observed_at` on every host endpoint. The legacy `status` field is derived
  from it, so a host behind a silent probe now reads `unknown` instead of
  `online`.
- Frontend on Next 15.5 and React 19, image and CI on Node 22.
- **Fonts are self-hosted** (Sora, Inter Tight, JetBrains Mono via
  `@fontsource-variable`): the frontend build and the UI need no access to
  Google Fonts, which also makes air-gapped builds possible.
- Dependency majors: lucide-react 1, framer-motion 14, vitest 5,
  @types/node 22. clickhouse-connect stays on 0.8 (1.x needs the aiohttp
  extra for its async client and is not yet tested against a real ClickHouse).

### Removed

- The 3D "gravity well" dashboard view and with it three.js and
  `@react-three/*`; the old sidebar layout, the glass-card components and the
  previous dashboard widgets.
- Many performance fixes: concurrent integration collection, bulk ping job,
  cheaper syslog inserts and queries, ClickHouse-side aggregation for trend and
  anomaly passes.

### Fixed

- **Security:** CSRF token required for cookie-authenticated `/api/v1`
  mutations; Telegram, Discord and webhook secrets encrypted and write-only;
  SSRF guards for webhooks, notification channels, host targets created
  through the API and AI provider URLs; JSON backup export encrypted with an admin
  passphrase; an install token could hijack an enrolled agent; LDAP sent
  passwords over unverified TLS and could take over local accounts; alert rules
  were reachable without login; integration credentials could be redirected to
  another server; changing a password requires the current one; five wrong
  passwords no longer lock the admin out from every browser.
- Updates no longer recreate the backend on an empty `/opt/repo/data`; the
  sidecar finds the database without a hard-coded container name.
- Password hashing survives bcrypt 5's 72-byte limit.

[Unreleased]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.6...v2.0.0
[2.0.0-rc.6]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.5...v2.0.0-rc.6
[2.0.0-rc.5]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.4...v2.0.0-rc.5
[2.0.0-rc.4]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.3...v2.0.0-rc.4
[2.0.0-rc.3]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.2...v2.0.0-rc.3
[2.0.0-rc.2]: https://github.com/jubacCH/Nodeglow/compare/v2.0.0-rc.1...v2.0.0-rc.2
[2.0.0-rc.1]: https://github.com/jubacCH/Nodeglow/releases/tag/v2.0.0-rc.1
