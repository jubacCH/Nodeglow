# Changelog

All notable changes to Nodeglow are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) for its releases.

Each release's section becomes its GitHub Release notes. Cut a release with
`scripts/release.sh X.Y.Z` (see [docs/INSTALL.md](docs/INSTALL.md#for-maintainers-cutting-a-release)).

## [Unreleased]

### Added
- Two-factor sign-in (TOTP). Every user can turn it on for their own account
  under the new **Account** page (account menu): scan a QR code with an
  authenticator app, confirm with a code, keep ten one-time recovery codes. Signing in then asks for
  the code after the password; LDAP accounts included. A code works once.
  Admins can turn it off for a user who lost their phone (Users page).
  Routes under `/api/account/mfa`; database revision 051.
- Account page: change your own password. Until now only admins could, on
  the Users page.
- Backup integrations (Proxmox Backup Server, Veeam, Synology) take per-job
  overrides of the "overdue" limit: one line per pattern, `weekly-* = 200`
  or `archive-vm = off`. A malformed line is reported, never silently
  ignored.
- PagerDuty, the way back: `POST /api/v1/events/pagerduty` applies an
  acknowledge or resolve done in PagerDuty to the Nodeglow incident.
- An integration's settings are checked when they are saved: a malformed
  Prometheus alert rule or backup override is refused with the line and the
  reason, instead of surfacing at the first collection.
- Certificate expiry incidents (`ssl_expiry`): a warning at 14 days left,
  critical at 3 days and when expired, resolved once the certificate is valid
  for longer again. Until now the days left were shown and never alerted on.
- Backup incidents (`backup`): a failed or overdue backup job opens an
  incident and resolves with the next successful run on schedule. The
  compliance job used to write its findings to the log only.
- Bulk "Maintenance for…" on the host list takes a duration
  (`maintenance_hours` on `PATCH /api/v1/hosts/bulk`).
- Host-down incidents. A host that fails its checks opens a `host_down`
  incident, for hosts the core checks, hosts behind a probe and agent hosts
  alike, after the offline grace period, and resolves when the host answers
  again. Before, a single host going down was a notification without an
  incident for core-checked hosts, and nothing at all for the others. The
  incident replaces the "Host offline" / "Host back online" notifications of
  the ping job and the rule `host_down_syslog` (syslog errors of the host are
  named in the summary).
- Heartbeat to an external monitor: with `NODEGLOW_HEARTBEAT_URL` set,
  Nodeglow requests that URL once a minute while its scheduler runs and
  Postgres answers. `/readyz` reports `scheduler_last_tick_seconds_ago`.
- A failed notification is retried after 5, 30 and 120 seconds. A channel
  that fails three deliveries in a row raises a self-check incident.
- The disk-space incident notifies when it opens and when it resolves.
- Incidents link to what they are about: "Open host" (one affected host) or
  "Open integration" in the header, and a link on every affected host. The
  incident API returns `integration` (id, type, name) for integration rules.
- Backups: every job says why it has its status (`status_reason`: overdue by
  how long, last run failed with which error, never ran).
- Thirteen integrations: Proxmox Backup Server, Veeam Backup & Replication,
  Synology Hyper Backup / Active Backup, Prometheus exporter scrape with
  alert rules, domain expiry (RDAP), Microsoft 365 / Entra ID, VMware
  vSphere, FortiGate, Kubernetes, NetBox (with optional host import),
  Hetzner Cloud, Infomaniak and Exoscale. They were built from the
  vendors' API documentation and tested against recorded-shape responses,
  not yet against live systems.
- On-call channels: PagerDuty (Events API v2), Opsgenie and Jira Service
  Management. One alert per incident (stable deduplication key), resolved
  when the incident resolves. A resolve reaches these channels regardless of
  the channel's minimum severity, so an alert is never left open there.
- Inbound events: `POST /api/v1/events` and an Alertmanager webhook receiver
  at `/api/v1/events/alertmanager` let other systems open, update and resolve
  incidents (rule `external_event`). See docs/API.md.
- API keys are also accepted as `Authorization: Bearer ng_…`, for senders
  that cannot set a custom header.
- Integration settings can have multi-line fields (alert rules, domain
  lists).
- Integration incidents. What an integration reports as a problem (a failed
  disk, a degraded RAID or pool, an expiring certificate, a UPS on battery, an
  unreachable DNS cluster node, ...) now opens an incident after two
  consecutive snapshots and resolves when it is gone (`integration_alert`).
  An integration that fails three collections in a row opens
  `integration_failed`, resolved by the next successful collection. Incidents
  of deleted or disabled integrations are resolved. Before, `parse_alerts()`
  was never called and these problems showed only on the detail page.
- Integration incidents name the affected ping host (the device an alert is
  about, else the integration's endpoint) and honour its maintenance window.
  Notifications go out after the commit, outside the snapshot write lock.
  `integration_failed` opens after two failed collections once the last
  success is ten minutes old.
- Alerts for UniFi (device disconnected, WAN down), Swisscom (connection
  down), Redfish (health, power off, temperature and fan sensors), Portainer
  (unreachable environment, restarting or unhealthy container), AdGuard
  (stopped, filtering off), Pi-hole (blocking off), firewalls (CPU, memory)
  and speedtest (minimum download/upload, off by default). Proxmox also
  checks the node root disk.
- Open integration incidents show on the integration's page (with links),
  as a count in the instance list and in the integration catalogue; the
  integration endpoints return them (`open_incidents`). Incident rules have
  plain names ("Integration not collecting").
- Integration alert limits per tenant (Settings → Monitoring → Integration
  alert limits): Proxmox node CPU/memory/disk, pool usage (Synology, TrueNAS,
  UniFi NAS), disk temperature (UniFi NAS), certificate expiry (NPM), UPS
  load, firewall CPU/memory, speedtest minimums.
- Release-signed agent updates: CI signs every agent binary with the agent
  release key (Ed25519; private key only in the vault and the GitHub secret
  `AGENT_RELEASE_SIGNING_KEY`), commits the signature next to the binary and
  attaches it to the GitHub Release. The release public key is compiled into
  the agent. Every agent of a multi-tenant installation accepts release-signed
  updates only; probes never apply an unsigned update (release signature or
  the local `update_public_key`); other single-tenant agents keep the pre-P2a
  paths. See docs/OPERATIONS.md#agent-updates.
- Enterprise multi-tenancy (licence feature `multi_tenancy`, `max_tenants`):
  operators create, suspend, resume and delete tenants under Administration →
  Tenants (deletion = 30 days suspended, then every PostgreSQL and ClickHouse
  row of the tenant is purged, its data keys destroyed first), assign
  retention plans (30 / 90 / 365 days),
  and see licence usage. The second tenant needs the ClickHouse tenant
  rebuild and PostgreSQL row-level security (or
  `NODEGLOW_TENANTS_WITHOUT_RLS=1`). Migration 045.
- Tenant switcher in the top bar (only with more than one tenant).
- Support access: operators see a tenant's data only while its admin has
  granted time-limited access (Settings → Support access); every access is
  in the tenant's audit log and a banner shows it to every member. It
  cannot manage the tenant's users, passwords, API keys or install tokens.
- Per-tenant envelope encryption: every tenant's credentials (integration
  configs, credentials, notification channel secrets, SMTP/phpIPAM
  passwords) are encrypted with the tenant's own data key, wrapped by a key
  derived from `SECRET_KEY` (table `tenant_keys`, revision 046). Existing
  values move to the tenant key when the new backend starts, not during the
  migration, so the old backend keeps working while an update runs.
  Destroying a tenant's keys makes its secrets unreadable everywhere
  (crypto-shred). `python -m services.tenant_keys verify` reports the
  formats and decrypt failures without printing secrets. JSON backups carry
  the encrypted values and the wrapped keys, so restoring one needs
  `SECRET_KEY` as well as the passphrase. A channel secret that does not
  decrypt fails only its own notification channel.
- Probe syslog relay: a probe can receive syslog (UDP/TCP, port 514 by
  default) inside its network and forward it over its agent connection;
  messages land in the probe's tenant and site and are matched against the
  hosts of that site. Switched on per probe through the agent API
  (`syslog_relay_enabled`, `syslog_relay_port`); needs the updated agent.
  See docs/OPERATIONS.md "Probe syslog relay".
- `NODEGLOW_SYSLOG_LISTENER` (`auto` / `on` / `off`) for the server's own
  syslog listener; `off` for hosted installations where syslog is
  probe-only.

### Changed
- Backup incidents are grouped: from three failed or overdue jobs of one
  source on, they are one incident ("45 backups overdue (proxmox)") that
  names the jobs, instead of one incident and one notification per job. A
  backup server that stops makes every job overdue at once.
- `docker-compose.yml` (installations run from a checkout) uses ClickHouse
  26.8 LTS; 24.8 is end of life. **An installation that still runs 24.8
  must not jump in one step**: set the image to 25.3, start, then 25.8,
  26.3 and 26.8, one start each, and only then pull this change. Each step
  takes a few seconds; syslog messages arriving during a restart are lost.
  `docker-compose.release.yml` and the CI stay on 24.8 for now.
- Updates wait until the new version runs and reports healthy (new step
  `health`). A release that does not come up is rolled back: the updater
  restores the previous compose file and `.env` and starts the previous
  version again. Before, `docker compose up` returning 0 counted as a
  successful update, and a release that crashed on start left the instance
  down. The database is not rolled back.
- Container logs are rotated (`local` driver, 5 files of 50 MB per service).
  They used to grow without limit.
- `multi_host_down` is keyed by subnet and `upstream_failure` by the failed
  parent, no longer by the exact set of hosts: an outage that spreads stays
  one incident, with its host list and count kept current. Incidents open
  under the old keys resolve once and reopen under the new ones.
- An incident's timeline gets an entry when something changed, not one per
  cycle. A day-long outage used to collect over a thousand identical lines.
- A backup job counts as overdue after its interval plus a quarter (at least
  two hours), not at the bare interval.
- The reason of an integration failure is cleaned of the web-reference link
  and followed by what the error usually means (login rejected, not
  reachable, certificate not trusted).
- Unfiltered `/api/bandwidth/history` returns the total over all interfaces
  per time bucket instead of the first 2000 raw samples.
- Telegram, Discord, webhook and e-mail notifications now carry a link into
  Nodeglow when the public URL is set (webhook payload field `url`). Before,
  only Teams, Slack and ntfy did. Host offline and back-online notifications
  link to the host list.
- Manual maintenance on a host defaults to two hours. "Until I end it" has to
  be chosen on purpose.
- The first-run welcome no longer prints install commands. Its links open the
  "Add host" and "Add agent" dialogs directly (`/hosts?add=1`, `/agents?add=1`).
- An agent's own log entries are attributed to its own host by name, no
  longer by the address the request came from (behind NAT that was the
  customer's router).

### Fixed
- Proxmox backups: a cluster watched by two integrations (one collecting,
  one standing by) no longer has two backup jobs per guest. The jobs of the
  standby were never updated and read as overdue although the backup ran
  every night. Existing duplicates are folded into one on the next
  collection, their history included.
- Proxmox backups: the job of a guest that was deleted is removed instead of
  staying overdue for ever. Only when every node of the cluster is online.
- "Template diversity spike" no longer fires on every agent host after a
  release. An agent that updates itself logs a dozen lines it has not sent
  in the past week (update found, verified, applied, unit stopped and
  started); the agent's own log and the systemd lines about its unit no
  longer count as new templates.
- Log lines with terminal colour codes are grouped into templates like any
  other. The codes sat directly in front of the timestamp, which was then not
  recognised, so every such line became a template of its own (the Nodeglow
  agent's log among them). Templates of coloured lines are learned afresh
  once.
- A sign-in while the backend is down or restarting said "Invalid username
  or password". It now says the server is not responding.
- Resolving an incident by hand sends the resolve notification. Before, an
  on-call channel kept its alert open after the incident was closed.
- Prometheus: a rule whose label set has no series yet (an error counter
  before the first error) no longer warns "matches no series"; only a
  metric that is missing altogether does. Found against a live exporter.
- Synology: a failed API call no longer puts the session id into the error
  text and the log.
- The DNS job deleted manually added hosts whose address another host also
  has, every 30 minutes and without a trace, and renamed hosts to their
  reverse-DNS name even when the user had named them. It now merges only
  what the scanner found, and keeps a given name.
- Certificate expiry was only read for hosts whose sole check is HTTPS.
- The heartbeat's database check raised a NameError and always answered
  "down", so the heartbeat would never have been sent.
- Incidents resolved as "back online" while the host was still down
  (`host_down_syslog` looked for fresh syslog errors, which a dead host does
  not send), and `upstream_failure` and `disk_space` were resolved a minute
  after every time they were raised. Each rule now resolves on its own
  condition, and a rule without one is left open.
- Two open incidents for one key no longer make the rule fail on every cycle.
- Backups page: the summary tiles showed dashes, "Needs attention" never
  appeared and no job was marked overdue, because the page read fields the
  API did not send. Every daily job showed "Warning" in the hours before its
  next run.
- Traffic page: the chart and the interface list stayed empty although data
  existed (the page took the response envelope for the list), and "0
  reporting" came from timestamps without a time zone.
- "Add agent" crashed the Agents page: the dialog read an enrollment key the
  server no longer sends. It now creates a per-install token (valid 24 hours,
  7 or 30 days), shows the finished command once, and says when the new agent
  has reported in. The install commands on the first-run welcome had no token
  and were answered with 401.
- Self-check incidents no longer swallow each other. Every self-check problem
  shared one dedup key, so while one incident was open or acknowledged (say
  "syslog stale"), a second problem ("ping_checks not completing") only became
  an event on it: no incident, no notification. Each problem now has its own
  incident; incidents under the old key are closed and reopened under the new.
- One host whose check cannot run (a hostname that is not a valid name, a
  malformed check type) no longer stops the ping cycle for every host of the
  tenant. That host is recorded as failed with the reason; the rest are
  checked and written as usual.
- SMTP connections have a 15 second timeout. A mail server that accepted the
  connection and then went silent held the notifying job indefinitely.
- A webhook or Discord URL refused by the address guard is logged as a failed
  notification, not as sent.
- The Topology page showed degraded and warning hosts as OK, because it read
  the old up/down status instead of the host state the rest of the app uses.
  It now counts them under "Warning" and includes them in "Problems".
- "Root cause" on the log patterns page always answered "Not found" (wrong
  API path).
- "Log volume anomaly" no longer flaps on hosts at their normal volume. The
  volume baseline and the template diversity shared one sample counter, so a
  host's first real hour was folded in at a fifth of its weight and its
  baseline sat far below the real rate (a host steady at 570/hr alerted
  against "112/hr ± 224"). Each now counts its own samples
  (`template_sample_count`, migration 050). The migration starts the volume
  baselines of host-keyed sources over; volume detection for them resumes
  after three days.
- "Template diversity spike" no longer fires on a busy hour of templates the
  host sends anyway (hourly cron, apt, a batch job). Only templates the host
  has not sent in the past week count towards the spike, and the incident says
  how many there were. Docker veth names (`veth74c2188`) are masked in
  templates, so a container restart no longer mints a fresh set of templates
  on every guest that shares the node's kernel log.
- The syslog TCP listener no longer closes a connection after 60 seconds
  without a message. rsyslog and syslog-ng keep one persistent connection and
  only notice the close on their next send, which failed ("omfwd: remote
  server ... seems to have closed connection", thousands a day per quiet
  host), lost that message and forwarded the error back as new noise.
  Connections now stay open (TCP keepalive frees the slots of senders that
  vanished); `NODEGLOW_SYSLOG_TCP_IDLE_TIMEOUT` restores an idle limit.
- Log volume baselines are learned per host (`host:<id>`) when a message has
  one, like the template diversity, instead of per sender address. Agents
  deliver through the frontend container, so every agent host shared one
  Docker address and their summed volume raised "Log volume anomaly" on a
  `172.18.0.x` source, flapping open and resolved around its threshold.
- Incidents resolved after ten quiet minutes no longer all say "error rate
  returned to normal": log anomalies, predictions, fleet patterns, severity
  trends, content anomalies and alert rules each name their own reason.
- Agent logs of hosts configured by FQDN (`ipam.example.com`) while the agent
  reports its short name (`ipam`) had no host. The syslog host cache now also
  knows a host by the first label of its name, unless two hosts share it.
- Template diversity is learned and checked per host instead of per source
  address. Agent logs arrive through the frontend container, so all agent
  hosts shared its address — which moved with every Docker restart and raised
  "Template diversity spike" incidents for a container IP. Incidents now name
  the host.
- Every integration showed as "Stale" and the catalogue as "no data": the
  integration endpoints sent snapshot times without a time zone, which browsers
  read as local time, so each snapshot looked an hour (summer: two) old. They
  now send UTC with `Z`.
- All API timestamps are UTC with `Z` (incidents, agents, hosts, API keys,
  users, syslog, SNMP, notifications, digest, ...). Several pages read the bare
  values as local time and were an hour off. A test refuses new bare
  `.isoformat()` calls in routers.
- UniFi counted wired clients once per device on the path (gateway and every
  switch); the client totals now come from the controller's client list.
- UniFi NAS: a RAID without a reported state (UniFi OS 6) showed as down, and
  an empty drive bay counted as a disk error. A RAID volume now takes name and
  health from its storage pool (matched by size) and is no longer shown twice;
  an empty bay shows as "Empty" and does not count as a disk.
- Cloudflare analytics come from the GraphQL Analytics API (24 h, hourly
  groups); the REST dashboard endpoint was retired by Cloudflare and had been
  shown as 0 requests. When analytics are unavailable the values are empty
  and a warning is logged. The SSL/TLS mode is read from the zone setting
  (was always "unknown"). Blocked threats no longer count as an alert.
- Swisscom Internet-Box listed its telephony accounts as connected devices.
- Parts an integration could not fetch are named on its page ("Not available
  in the last collection") instead of showing as empty, and logged as a
  warning: Cloudflare firewall events (now from the GraphQL API; the REST
  endpoint answers 404 and the events had always been empty), Proxmox tasks
  and backup storages (an unreadable storage list showed "No backup" for every
  guest), Portainer containers per environment, UniFi events and speedtest.
- UniFi gateway speedtest: "latest" was the oldest result (the controller
  ignored the sort order), and an all-zero run next to every real one drew a
  dip to 0 into the chart. Results are sorted newest first and empty runs
  dropped; the page shows the age of the last test and says when there is no
  newer result.
- Speedtest: `server_name` is now the server's operator and `server_location`
  its city and country; both parsers had them the other way round. Migration
  049 swaps the two fields in stored speedtest snapshots. phpIPAM showed 1970
  as "last seen" for addresses never seen.

### Security
- The audit log is append-only for the application. On an installation with
  its own app database user (`POSTGRES_APP_USER`, the default of
  `install.sh`), the app role can add and read audit entries but no longer
  change or delete them. The tenant purge removes a tenant's entries over
  the owner connection. Installations with a single database user are
  unchanged: there the app owns the table.
- The audit log records what it left out: users created, deleted and
  changed in role, passwords reset or changed, API keys and agent install
  tokens created and revoked, credentials created, changed and deleted,
  updates started, and failed sign-ins (with the name as typed, never the
  password). None of these wrote an entry before.
- The first-run setup asks for a setup token (`NODEGLOW_SETUP_TOKEN`, printed
  by `install.sh`; otherwise generated, logged and stored in
  `/data/setup-token`). Before, whoever reached the port first could create
  the admin account. The setup page now also shows why a submission was
  refused.
- Address guard at check time. A host on a loopback, link-local or cloud
  metadata address is not checked at all, whatever its check type, and HTTP
  checks connect through the guarded transport. Hosts added by the subnet
  scanner are validated like hosts added by hand. Before, a host could reach
  the scheduler on a path that skipped validation (scanner import, bulk
  edit), and an HTTP check with a keyword then read such an address.
- The agent installers write `config.json`, which holds the agent's token,
  readable by root only (Linux) and by SYSTEM and Administrators only
  (Windows). It used to be world-readable.
- CSV exports keep cells that start with `=`, `+`, `-` or `@` as text. A
  hostname or syslog message, which any device on the network can set, could
  otherwise run as a formula when the export is opened in a spreadsheet.
- Dependency updates for new advisories. Frontend: Next.js 15.5.29
  (GHSA-2464-fhr4-w9xw, remote code execution in the Image Optimization API
  with SVG files; GHSA-p3m5-59mp-jv5r), DOMPurify 3.4.17 and undici 8.12.4.
  Agent: quinn-proto 0.11.19, rustls 0.23.46, rustls-webpki 0.103.16 and
  webpki-roots 1.0.12 (RUSTSEC-2026-0338 to -0342, -0346 to -0349, -0381,
  -0382). Backend: OpenTelemetry 1.45.2 / 0.66b2 (CVE-2026-114603). The
  backend image now applies pending Debian security updates at build time,
  which brings in the fixed zlib1g (CVE-2026-76844, CVE-2026-85091).

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
