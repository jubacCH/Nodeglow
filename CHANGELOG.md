# Changelog

All notable changes to Nodeglow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for its releases.

Each release's section becomes its GitHub Release notes. Cut a release with
`scripts/release.sh X.Y.Z` (see [docs/INSTALL.md](docs/INSTALL.md#for-maintainers-cutting-a-release)).

## [Unreleased]

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
- **E3 redesign.** New design system, app shell (icon rail, top bar, section
  navigation) and every page migrated to it: hosts, incidents, alert rules,
  maintenance, logs, integrations, agents, topology, settings and more.
- **Dashboard v2.** The Overview route shows the new E3 dashboard on
  `/api/v2/dashboard` and `/api/v2/summary`, plus a change feed (`/changes`)
  and the last dashboard visit per user.
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
- **Unified host state:** one probe-aware host state for lists, dashboard and
  topology; incidents record their affected hosts.
- Frontend on Next 15.5 and React 19, image and CI on Node 22.
- Many performance fixes: concurrent integration collection, bulk ping job,
  cheaper syslog inserts and queries, ClickHouse-side aggregation for trend and
  anomaly passes.

### Fixed

- **Security:** CSRF token required for cookie-authenticated `/api/v1`
  mutations; Telegram, Discord and webhook secrets encrypted and write-only;
  SSRF guard for webhooks; JSON backup export encrypted with an admin
  passphrase; an install token could hijack an enrolled agent; LDAP sent
  passwords over unverified TLS and could take over local accounts; alert rules
  were reachable without login; integration credentials could be redirected to
  another server; changing a password requires the current one; five wrong
  passwords no longer lock the admin out from every browser.
- Updates no longer recreate the backend on an empty `/opt/repo/data`; the
  sidecar finds the database without a hard-coded container name.
- Password hashing survives bcrypt 5's 72-byte limit.

[Unreleased]: https://github.com/jubacCH/Nodeglow/commits/main
