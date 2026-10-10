# Nodeglow

Self-hosted infrastructure monitoring with **log intelligence** and
**incident correlation**: host checks, remote probes, a Rust agent, syslog,
SNMP and 19 integrations in one place, with one consistent answer to "what is
broken right now, and what changed since I last looked".

Nodeglow runs as a small Docker Compose stack (PostgreSQL, ClickHouse, a
FastAPI backend, a Next.js UI and an update sidecar) on a single Linux host.
It is Open Core: the core is AGPL-3.0, a few enterprise features are
source-available under a commercial license (see [Editions](#editions)).

---

## Features

**Monitoring**

- **Host checks** — ICMP, TCP and HTTP(S) per host. HTTP checks can set
  method, path, expected status, a keyword and redirect handling, and record
  why a check failed.
- **One host state everywhere** — `up`, `degraded`, `warning`, `down`,
  `unknown`, `maintenance`, `disabled`, computed by one probe-aware rule for
  lists, badges, dashboard and topology. No fresh data means `unknown`, never
  a stale green.
- **Remote probes** — let an enrolled agent run the checks for hosts in
  networks the server cannot reach; a silent probe turns its hosts `unknown`.
- **Agent** — Rust agent for Linux and Windows: CPU, memory, disks, network,
  processes, Docker, temperatures, journal / Windows Event Log, watched
  services (a stopped service raises an incident), signed auto-update.
- **Integrations** — 19 built-in collectors (table below), one Python file
  each.
- **SNMP** — MIB upload, OID browser, polling with thresholds.
- **Certificates** — TLS expiry tracking, discovered by the network scanner.
- **Discovery and scans** — subnet scans with port/service identification and
  an inbox of things to review (new ports, certificates).
- **Uptime and SLA** — 24 h / 7 d / 30 d availability, history heatmaps,
  latency thresholds per host or globally.

**Logs**

- **Syslog receiver** — UDP/TCP (RFC 3164/5424), stored in ClickHouse, with
  host auto-assignment, full-text search, a live tail that filters "severity
  N or worse", and an optional sender allowlist.
- **Log intelligence** — template extraction ("log patterns"), auto-tagging,
  noise scoring, burst detection, per-host rate baselines with spike and
  silence detection, and precursor patterns learned from past incidents.
- **Traffic** — interface bandwidth and top talkers.

**Alerting and incidents**

- **Incident correlation** — groups related failures (several hosts down,
  syslog + ping, integration + host) into one incident with its affected
  hosts.
- **Alert rules** — custom conditions (contains, regex, numeric operators) on
  any field.
- **Notifications** — Telegram, Discord, e-mail, webhook, Microsoft Teams,
  Slack and ntfy. Channel secrets are encrypted and write-only.
- **Maintenance windows** — one-off and recurring; hosts in a window are
  shown as `maintenance` and do not alert.

**Operations**

- **Overview dashboard** — health, open incidents, internet/WAN, topology,
  groups, latency, syslog, availability and "since your last visit", from a
  single `GET /api/v2/dashboard`; a full change feed on `/changes`.
- **Weekly report** — scheduled e-mail summary of incidents, availability,
  syslog and certificate expiry.
- **Self-monitoring** — Nodeglow raises ordinary incidents when one of its own
  jobs or data sources stops working.
- **Users and access** — admin / editor / read-only roles, optional LDAP,
  API keys with the same roles, audit log.
- **Updates and backups** — one-click updates with signature verification,
  a database dump before every update and a daily scheduled dump, encrypted
  JSON export.
- **AI (opt-in)** — off until an admin enables it; Anthropic, Azure OpenAI or
  any OpenAI-compatible endpoint including local models (Ollama, vLLM, LM
  Studio); personal data and secrets are redacted before anything is sent.
  The AI features themselves are part of the enterprise edition.
- **UI** — keyboard-driven (command palette, `g …` shortcuts), light, dark or
  system theme, works on phones; fonts are self-hosted, nothing is loaded from
  third-party CDNs.

### Integrations

| Integration | What is monitored |
|---|---|
| **Proxmox VE** | Nodes, VMs, LXC containers — CPU, RAM, disk, IO rates |
| **UniFi** | APs, switches, clients, signal strength, PoE ports |
| **UniFi NAS** | Storage, volumes, RAID |
| **Pi-hole** | Query stats, blocking %, top domains |
| **AdGuard Home** | Query stats, blocking %, filter lists |
| **Technitium DNS** | Queries, blocking, cluster health, available updates |
| **Portainer** | Docker containers across all endpoints |
| **TrueNAS** | Pools, datasets, alerts, system info |
| **Synology DSM** | Volumes, shares, CPU, RAM, SMART |
| **pfSense / OPNsense** | Interface stats, rules, DHCP leases |
| **Home Assistant** | Entity states, system info |
| **Gitea** | Repos, users, issues, system stats |
| **phpIPAM** | Subnets, address utilisation, import into Hosts |
| **Speedtest** | Download, upload, latency — Ookla CLI if bundled (`BUNDLE_OOKLA=1`, own builds only), otherwise `speedtest-cli` |
| **UPS / NUT** | Battery charge, on-line / on-battery, runtime |
| **Redfish / iDRAC** | Server temperatures, fans, power, system info |
| **Swisscom Internet-Box** | WAN status, connected devices, device info |
| **Cloudflare** | Zones, DNS records, analytics, security events |
| **Nginx Proxy Manager** | Proxy hosts, certificates and their expiry, redirections, streams |

---

## Editions

| | Community | Enterprise |
|---|---|---|
| License | [AGPL-3.0-only](LICENSE) | AGPL core + [Nodeglow Enterprise License](ee/LICENSE) (draft) for `ee/` |
| Image | `nodeglow-backend-community` (or `NODEGLOW_DISABLE_EE=1`) | `nodeglow-backend` (default) |
| Everything listed above except the AI features | ✓ | ✓ |
| High availability (scheduler leader election across backends) | — | ✓ |
| Glow AI assistant, AI postmortems, AI daily summary | — | ✓ |

The default image contains `ee/`, but the enterprise features stay inactive
until an offline-verified license key is installed (Settings → License, or
`NODEGLOW_LICENSE`). Without a key it behaves exactly like the community
edition. Planned enterprise features include multi-tenancy for MSPs
([design, in progress](docs/specs/2026-10-10-multi-tenancy-design.md)), SSO
and SCIM, custom roles, on-call escalation and per-customer SLA reports.
Details: [LICENSING.md](LICENSING.md), [ee/README.md](ee/README.md).

---

## Quick start

### Install a release (recommended)

Signed multi-arch images from GHCR, no build on your host:

```bash
curl -fsSLO https://github.com/jubacCH/Nodeglow/releases/latest/download/install.sh
sudo sh install.sh            # → /opt/nodeglow, prints the URL when ready
```

`install.sh` checks the prerequisites, generates `.env` with fresh secrets,
pulls and (with cosign installed) verifies the images, and starts the stack.
Air-gapped: `sudo sh install.sh --offline nodeglow-X.Y.Z-offline-amd64.tar.gz`.
Then open `http://<host>:8000` and finish the setup wizard right away — it
creates the admin account.

Requirements: Linux (x86_64 or arm64), Docker Engine 20.10+ with Compose v2,
2 vCPU / 4 GB RAM / 20 GB SSD. Everything else — editions, upgrades, backups,
signature verification — is in **[docs/INSTALL.md](docs/INSTALL.md)**.

### Run from source

For development, or if you want to build the images yourself:

```bash
git clone https://github.com/jubacCH/Nodeglow.git nodeglow
cd nodeglow

# Required: compose refuses to start without these two secrets.
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^UPDATE_SIDECAR_TOKEN=.*/UPDATE_SIDECAR_TOKEN=$(openssl rand -hex 32)/" .env
# Strongly recommended: the key that encrypts stored credentials.
# Back it up separately — no database backup contains it.
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env

APP_VERSION=$(head -n1 VERSION) docker compose up -d --build
```

Open **http://localhost:8000**. A checkout-based installation updates itself
from `main` (git mode of the updater); see
[OPERATIONS.md → Updating](docs/OPERATIONS.md#updating). Frontend development
with hot reload: [frontend/README.md](frontend/README.md).

### Install an agent

Create an install token under **Infrastructure → Agents & Probes**; the page
shows the ready-made command (tokens expire, default 24 h):

```bash
# Linux
curl -fsSL 'http://YOUR_SERVER:8000/install/linux?token=<INSTALL_TOKEN>' | sudo bash
```

```powershell
# Windows (PowerShell as Administrator)
irm 'http://YOUR_SERVER:8000/install/windows?token=<INSTALL_TOKEN>' | iex
```

The agent enrolls with its token, creates its host, reports every 30 s,
checks for updates every 5 minutes (SHA-256, plus Ed25519 when update signing
is configured) and can be switched into probe mode from the UI.

---

## Architecture

```
          browser ──► frontend (Next.js 15, :8000) ──► backend (FastAPI, :8000 internal)
agents / probes ──────────────┘                         │  API, scheduler, syslog receiver,
syslog senders ──► 514/udp, 1514/tcp ───────────────────┤  collectors, correlation, alerting
                                                        ├──► PostgreSQL 16  (config, hosts, incidents, patterns)
                                                        ├──► ClickHouse 24.8 (pings, metrics, syslog — TTL)
                                                        └──► updater sidecar (updates, scheduled dumps)
```

| Part | Directory | Stack |
|---|---|---|
| Backend | `backend/` | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, APScheduler |
| Enterprise plugin | `ee/backend/` | loaded by `backend/ee_loader.py` through `backend/extensions.py` |
| Frontend | `frontend/` | Next.js 15, React 19, TypeScript, Tailwind, TanStack Query, ECharts; Node 22 |
| Agent | `agent/` | Rust (Tokio, reqwest), Linux and Windows |
| Updater | `sidecar/` | Python, Docker socket; git mode (build from checkout) or image mode (signed releases) |
| Releases | `.github/workflows/release.yml`, `scripts/` | GHCR images, cosign, `install.sh`, offline bundle |

The backend is a single process that runs the API, the scheduler, the syslog
receiver and all collectors. Collectors write snapshots to PostgreSQL and time
series to ClickHouse; the correlation engine and alert rules turn them into
incidents; the UI reads everything through the REST API (`/api/v1`,
`/api/v2`) and a WebSocket. Design and API details: [docs/README.md](docs/README.md).

---

## Documentation

| | |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Installing and upgrading a release, editions, offline installs, verifying signatures |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Sizing, ports, hardening, updates, backups, the encryption key, license, AI, troubleshooting, **configuration reference** |
| [docs/API.md](docs/API.md) | REST API: authentication, v1 and v2 endpoints |
| [docs/README.md](docs/README.md) | Index of all documentation, current and historical |
| [CHANGELOG.md](CHANGELOG.md) | Changes per release |

---

## Contributing

Bug reports, fixes, integrations and docs are welcome. Contributions need the
[CLA](CLA.md) (once) and a DCO sign-off on every commit — see
[CONTRIBUTING.md](CONTRIBUTING.md). Security issues: please use GitHub's
private vulnerability reporting, not a public issue.

## License

Everything outside `ee/` is licensed under the
[GNU AGPL-3.0-only](LICENSE). The `ee/` directory is source-available under
the [Nodeglow Enterprise License](ee/LICENSE) (draft): free for development
and testing, production use needs a license. Details and commercial
licensing: [LICENSING.md](LICENSING.md). Third-party licenses:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
