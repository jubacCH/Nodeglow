# Operating Nodeglow

What you need to run this in production: updating, backing up, restoring, and
working out what is wrong when something looks off.

For installation see the Quick start in the [README](../README.md).

---

## Before you go live

### System requirements and sizing

Nodeglow runs as **one backend process** (a single uvicorn worker running the
API, the scheduler, the syslog receiver and all collectors) next to
PostgreSQL, ClickHouse, the Next.js frontend and the updater. It is built and
run at homelab / small-network scale; the numbers below come from the code and
from that experience, not from a load test.

| | Minimum | Comfortable |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB SSD | 50 GB+ SSD, depending on syslog volume |
| OS | Linux with Docker Engine 20.10+ and Compose v2 | |

- The Postgres defaults in `docker-compose.yml` (`shared_buffers` 512 MB,
  `effective_cache_size` 1.5 GB) assume a 4–8 GB host. Lower them via
  `POSTGRES_SHARED_BUFFERS` / `POSTGRES_EFFECTIVE_CACHE_SIZE` on smaller hosts.
- ClickHouse alone wants about 1 GB of RAM and is what grows with syslog
  volume. Its tables are TTL-reaped; plan disk for your retention × message
  rate.
- An update needs **2 GB free** (preflight check) for the image build.
- **Practical ceiling: roughly 1000 monitored hosts** at the default 60 s
  interval. Checks run 50 at a time, and an unreachable host holds its slot
  for the full timeout (2 s ping, 3 s TCP, 5 s HTTP), so many hosts down at
  once stretches a round towards the interval. Scheduled jobs are
  `max_instances=1`: a round that overruns is skipped, not stacked, and the
  self-check raises an incident when a job stops completing.
- Scaling out is not supported out of the box. `REDIS_URL` makes the rate
  limiter and job leadership multi-process safe, but the syslog listener and
  the compose file assume a single backend.

### Ports and firewall

| Port | Proto | Direction | Purpose | Expose to |
|---|---|---|---|---|
| 8000 | tcp | inbound | Web UI and API (frontend, proxies to the backend) | Users and agents; put TLS in front |
| 514 | udp | inbound | Syslog (mapped to container 1514) | Devices that send logs |
| 1514 | tcp | inbound | Syslog over TCP | Devices that send logs |
| 8000 (backend), 5432, 8123, 9100 | tcp | internal only | Backend, Postgres, ClickHouse, updater | Docker network only — never publish |
| varies | icmp/tcp/udp | outbound | Checks, integrations (HTTPS APIs), SNMP 161/udp | Monitored networks |
| 443 | tcp | outbound | Notifications (Telegram, Discord, webhooks), GeoIP updates, GitHub for updates | Internet |
| 25/465/587 | tcp | outbound | E-mail notifications, if configured | Your SMTP server |

**Agents** connect *to* the server on the UI port (8000, or 443 behind a
reverse proxy) and need no inbound port on the agent host. Use the address
agents can reach in the install command — the server's own idea of its address
is wrong behind NAT or a proxy.

`UI_BIND` and `SYSLOG_BIND` in `.env` restrict which host address the ports
bind to — `UI_BIND=127.0.0.1` when a reverse proxy on the same host is the only
way in.

### Hardening checklist

- [ ] `POSTGRES_PASSWORD` and `UPDATE_SIDECAR_TOKEN` are random
      (`openssl rand -hex 32`), not the `.env.example` values.
- [ ] `SECRET_KEY` is set in `.env` and escrowed separately
      ([The encryption key](#the-encryption-key)); the backend logs no
      SECRET_KEY warning on start.
- [ ] TLS terminates at a reverse proxy (Caddy, nginx, Traefik, NPM) in front of
      port 8000; plain 8000 is not reachable from untrusted networks
      (`UI_BIND=127.0.0.1` or a firewall rule).
- [ ] Syslog ports are reachable only from the networks that send logs
      (`SYSLOG_BIND` or a firewall rule); consider the syslog host allowlist in
      Settings.
- [ ] Backups are copied off the host
      ([Get the backups off the host](#get-the-backups-off-the-host)), and a
      restore has been tried once.
- [ ] The updater port (9100) is not published, and nothing but the backend
      can reach it.
- [ ] Agents are enrolled with per-install tokens;
      `NODEGLOW_ALLOW_SHARED_ENROLLMENT` is unset.
- [ ] Optional: `NODEGLOW_NO_NEW_PRIVILEGES=true`, after confirming ICMP checks
      still work (see the comment in `docker-compose.yml`).
- [ ] `DEBUG` is unset (it exposes the OpenAPI docs).

---

## Updating

### From the UI

**System → Status → Software Updates → Update Now.**

The update runs as an observable sequence and the page shows each step live:

| Step | What happens | If it fails |
|---|---|---|
| `preflight` | Checks free disk, Docker socket, database container, clean working tree, `HEAD` on `main`, and that `/data` would stay the same host directory | Nothing has been changed yet |
| `backup` | `pg_dump` of the whole database, gzipped, retention 5 | Nothing has been changed yet |
| `pull` | Fast-forwards the repo to `origin/main` | Repo updated, containers untouched — rerunning is safe |
| `build` | Builds the new images | Old containers keep serving |
| `migrate` | Applies migrations using the *new* code, before it goes live | Old containers keep serving; the backup name is shown |
| `restart` | Recreates the application containers | Shown as failed; the backup is available |

**A failed step stops the chain.** Everything except a failure inside `restart`
itself leaves the running installation serving as before.

There is no automatic rollback — the backup taken in step 2 is the recovery
path, deliberately, so that a bad rollback cannot compound a bad update.

### Common preflight failures

**`Working tree is dirty: ...`** — a file was changed or added inside the
installation directory. The message names the files. Editing `docker-compose.yml`
or `.env` in place is the usual cause; `.env` backups are already ignored.
Move the file out of the directory, or commit it if it is a deliberate local
change.

**`HEAD is not on refs/heads/main`** — the checkout is on a branch or detached.
`git checkout main` in the installation directory.

**`Not enough free disk space`** — under 2 GB free. The build needs room for new
images; old ones can be reclaimed with `docker image prune`.

**`Refusing to update: compose would mount X as /data, but the running container
uses Y`** — the updater could not work out the host directory of the stack,
and recreating the containers would have attached them to a different (empty)
data directory. Nothing has been changed. Set `HOST_PROJECT_DIR` in `.env` to
the directory the stack was started from (see below), then
`docker compose up -d updater` and retry.

### Updater: host paths

The updater sees the repository at `/opt/repo`, but the stack runs from some
other directory on the host (e.g. `/opt/vigil`). Compose turns relative bind
sources such as `./data` into absolute paths *before* handing them to the
Docker daemon, which then reads them as **host** paths. Older updater versions
therefore recreated `nodeglow` with `/opt/repo/data` — a directory that only
existed because Docker created it, empty — instead of the real `./data`.

The updater now:

1. takes the host directory from `HOST_PROJECT_DIR` if set, otherwise from the
   `com.docker.compose.project.working_dir` label of the running `nodeglow`
   container (written by compose when you ran `docker compose up` there);
2. links that path to `/opt/repo` inside its own container, so compose can
   read the compose file, `.env` and build contexts under the host path;
3. runs every compose command (`build`, the migration `run --rm`, `up`) with
   `--project-directory <host dir>`, so bind sources and `.env` resolve as on
   the host;
4. refuses the update in `preflight` if the `/data` that compose would mount
   differs from the one the running container uses.

A label that points at `/opt/repo` itself is ignored: it means the running
container was created by an old updater with the broken paths. In that case
set `HOST_PROJECT_DIR` explicitly, and recreate the backend once by hand from
the installation directory (`docker compose up -d nodeglow`) so it is attached
to the real `./data` again.

**Cleaning up:** if a directory `/opt/repo` exists on the *host* and your
installation lives elsewhere, it is a leftover of that bug (typically holding
only an empty `data/geoip`). Check that no running container uses it —
`docker ps -q | xargs docker inspect -f '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' | grep /opt/repo`
must print nothing — then delete it.

### From the command line

```bash
cd /path/to/nodeglow
git pull --ff-only
APP_VERSION=$(head -n1 VERSION) docker compose build nodeglow frontend
docker compose run --rm --no-deps nodeglow python migrate.py   # optional: verify first
docker compose up -d --no-deps nodeglow frontend
```

`APP_VERSION` carries the release number from `VERSION` into the image (the
backend build context does not contain that file). Without it the build still
works, but the UI and API report the version as `0.0.0+unknown`. The UI update
does this automatically.

Migrations also run automatically on every container start, so the explicit
`migrate.py` call is only there if you want to see the result before restarting.
`SKIP_MIGRATIONS=1` in the environment bypasses it — for emergencies only, since
running new code against an old schema is what it exists to prevent.

### Upgrade policy

- **`main` is the release channel.** The updater only ever fast-forwards to
  `origin/main`; there are no release branches or tags to pick from yet.
  `VERSION` is bumped by hand and is informational.
- **Updates are forward-only.** Migrations have no tested downgrade path. The
  way back from a bad update is restoring the `pre-update-*` dump together with
  the previous commit (`git checkout <old sha>` + rebuild), not a downgrade
  migration.
- **Update regularly rather than in big jumps.** Every migration runs on every
  deploy path, so skipping releases works, but a small step is easier to
  diagnose if something fails.
- **Before updating:** check that the last scheduled backup is recent
  (`docker compose logs updater | grep "scheduled backup"`), and read the
  commit list in *Software Updates* for anything marked as needing an `.env`
  change.
- **Agents** update themselves from the server after the server is updated
  (SHA-256 checked; additionally ed25519-verified when update signing is
  configured on the server and the agent has the public key).

---

## Backups

A complete recovery needs **three** things. The automatic backups cover only
the first:

| What | Where it lives | Backed up automatically? |
|---|---|---|
| PostgreSQL database | `pgdata` volume | Yes — daily, and before every update |
| The encryption key (`SECRET_KEY`) | `.env`, or `./data/.secret_key` | **No — by design, see below** |
| `.env` (passwords, tokens) | installation directory | No |

Without the encryption key a restored database is still usable, but every
stored credential (integration passwords, API tokens, SNMP communities, SMTP
password) is unreadable and has to be re-entered.

### What is backed up automatically

The updater sidecar writes gzipped `pg_dump` files into the `backups` Docker
volume:

| File | When | Kept |
|---|---|---|
| `scheduled-<timestamp>.dump.gz` | Daily at 02:30 UTC (`BACKUP_SCHEDULE`) | `BACKUP_RETENTION` (5) |
| `pre-update-<timestamp>.dump.gz` | Before every update, prior to migrating | `BACKUP_RETENTION` (5) |

The two kinds are pruned separately, so daily dumps never push the last
pre-update dump out. A scheduled dump is skipped while an update runs (the
update takes its own), and an update requested during a dump is refused with
409 until the dump is done.

```bash
docker compose exec updater ls -lh /backups/
docker compose logs updater | grep "scheduled backup"   # ok / FAILED per run
```

`GET /api/update/backups` (admin session) lists the dumps and, under
`schedule`, the last scheduled result and the next slot.

`BACKUP_SCHEDULE` accepts `HH:MM` (daily, container clock = UTC),
`every 6h` / `every 90m` (minimum 15 minutes), or `off`. An invalid value
disables scheduled backups and says so in `docker compose logs updater`.

PostgreSQL holds configuration, hosts, rules, incidents and learned patterns —
everything that cannot be recomputed. ClickHouse holds the time series (ping
results, syslog, metrics) and is **not** included: it is reaped by TTL anyway
and would dominate the dump size. Losing it loses history, not configuration.

### Get the backups off the host

The `backups` volume lives on the same disk as the database. It protects
against a bad migration or an operator mistake, **not** against losing the
host. Copy the dumps somewhere else on a schedule — a NAS, object storage,
another machine:

```bash
# on the Docker host, e.g. from cron after 02:30 UTC
docker run --rm -v <project>_backups:/backups:ro -v /mnt/nas/nodeglow:/out alpine \
  sh -c 'cp -n /backups/*.dump.gz /out/'
```

(`<project>` is the compose project name — `docker volume ls | grep backups`.)
Off-host copying is not built in; it is the most important thing to add
yourself.

### Taking one manually

```bash
docker compose exec -T db pg_dump -U nodeglow -Fc nodeglow | gzip > backup.dump.gz
```

### Restoring

```bash
# Stop the application so nothing writes while restoring
docker compose stop nodeglow frontend

gunzip -c backup.dump.gz | docker compose exec -T db \
  pg_restore -U nodeglow -d nodeglow --clean --if-exists

docker compose start nodeglow frontend
```

The container applies any outstanding migrations on start, so restoring an
older dump onto newer code is safe. Check the backend log afterwards: the line
`Encryption key fingerprint: …` must match the fingerprint you escrowed with
the key (next section), otherwise the restored credentials will not decrypt.

---

## The encryption key

`SECRET_KEY` encrypts every stored credential (Fernet) and peppers API-key
hashes. It is the one piece of state that no backup in this stack contains —
deliberately: a backup that holds both the ciphertext and the key protects
nothing once it leaks.

### Where it is

- **`SECRET_KEY` in `.env`** (recommended). The backend then logs nothing
  about it.
- **Not set:** the backend uses `./data/.secret_key` in the installation
  directory, creating one on first start. It logs a warning banner on every
  start, and an ERROR banner when it had to *generate* a new key — that is the
  moment to stop if you expected an existing key, because credentials in a
  restored database are now unreadable.

Move a file-based key into `.env` (the value must be identical):

```bash
cat ./data/.secret_key            # copy this value
# add SECRET_KEY=<value> to .env, then:
docker compose up -d nodeglow     # the warning banner must be gone
rm ./data/.secret_key
```

### Escrow it

Store the key **separately** from the database backups: a password manager or
secrets vault, or a sealed printout. Store its fingerprint next to it, so you
can later prove that a key belongs to an installation or a backup without
showing the key:

```bash
docker compose logs nodeglow | grep "Encryption key fingerprint"
# or
docker compose exec nodeglow python -c "from config import secret_key_fingerprint as f; print(f())"
```

To compute the fingerprint of an escrowed key on any machine with Python
(the key is read from the terminal, not from the command line):

```bash
python3 -c "import getpass,hashlib; k=getpass.getpass('key: ').encode(); \
  print(hashlib.sha256(b'nodeglow-secret-key-fingerprint:'+k).hexdigest()[:16])"
```

The JSON export (`GET /api/v1/backup`) records the fingerprint of the key it
was made with; a JSON restore under a different key logs an error and returns
a `warning` field.

### Changing it

There is no re-encryption tooling. Setting a different `SECRET_KEY` makes all
existing credentials unreadable; they then have to be entered again. Treat the
key as permanent for the life of the installation.

### Recovering after losing the host

1. Install Nodeglow on the new host (README, Quick start), but before the
   first `docker compose up` put the **original** `SECRET_KEY` and
   `POSTGRES_PASSWORD` into `.env`. Generate a new `UPDATE_SIDECAR_TOKEN`.
2. `docker compose up -d db` and wait until it is healthy.
3. Restore the newest off-host dump as in [Restoring](#restoring) (the
   `pg_restore` part; `nodeglow` and `frontend` are not running yet).
4. `docker compose up -d`.
5. Compare `Encryption key fingerprint` in the backend log with the escrowed
   one, then spot-check an integration that uses a stored password.

Syslog and metric history (ClickHouse) start empty; configuration, hosts,
rules and incidents are back.

---

## When something looks wrong

### Nodeglow tells you first

A `self_check` job runs every five minutes and raises **ordinary incidents**
when Nodeglow stops working properly:

- a data source that is in use stops receiving data
- a scheduled job stops completing successfully

Those appear in the incident list like any other alert. This exists because
silent failure is the worst outcome for a monitoring product: the UI keeps
rendering while nothing is collected. Check the incident list before assuming
the platform is healthy.

### Health at a glance

```bash
docker compose ps                    # all containers should be healthy
curl -s localhost:8000/health        # backend liveness
```

### Are the collectors actually collecting?

```bash
# Scheduler jobs: divide sum by count for the average duration
docker compose exec nodeglow python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())" \
  | grep -E "job_runs_total|job_duration_seconds_sum"
```

A job with only `status="failure"` and no `status="success"` has never worked.
A `last_success_timestamp` far in the past means it has stopped.

### Database load

`pg_stat_statements` is enabled, so the expensive queries are visible:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c \
  "SELECT round(total_exec_time) ms, calls, left(query,80)
     FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10"
```

Sequential scans on large tables usually mean a missing index:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c \
  "SELECT relname, seq_scan, seq_tup_read, n_dead_tup,
          pg_size_pretty(pg_total_relation_size(relid)) AS size
     FROM pg_stat_user_tables ORDER BY seq_tup_read DESC LIMIT 10"
```

### Disk usage

ClickHouse keeps its own telemetry under TTL, and Postgres reclaims space
through autovacuum, which is tuned more aggressively on the churn-heavy tables.
If a table is far larger than its live row count suggests, it is bloated from
past deletions and needs a one-off compaction:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c 'VACUUM FULL <table>'
```

This takes an exclusive lock for the duration — seconds for a small table, but
plan for it on a large one.

### Logs

```bash
docker compose logs nodeglow --tail 200
```

Prefer `--tail` over `--since` on a long-running container: `--since` still
scans the entire log and can take minutes.

`LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default `INFO`) and
`LOG_FORMAT` (`text` or `json`) in `.env` control the backend log; restart the
`nodeglow` container after changing them. `json` writes one object per line
(`ts`, `level`, `logger`, `msg`, `request_id`, extras, `exc`) for Loki, Elastic
and similar. `DEBUG` also shows why integrations skipped optional data and
re-enables the per-run chatter of APScheduler and httpx.

Every HTTP request carries an ID: the `X-Request-ID` from your reverse proxy if
it sends one, otherwise a generated one. It is returned in the `X-Request-ID`
response header and appears in every log line written while handling that
request, so an error a user reports can be found with
`docker compose logs nodeglow | grep <id>`.

---

## AI features

Glow (the chat), automatic incident postmortems and the daily AI summary send
data to a language model. They are **off until an admin opts in** under
Settings → AI. While they are off nothing is sent: Glow and the postmortem
button answer `409 {"code": "ai_disabled"}`, automatic postmortems and the
daily summary are skipped, and the UI says how to enable AI.

The switch is per installation today. All AI settings are read through one
function (`services/ai_config.load_ai_config`), so when tenants arrive the
scope changes there and not in every feature.

### Upgrading an installation that already used AI

Before the opt-in existed, configuring a Claude API key was the opt-in. On the
first start after the upgrade, an installation that **already has a Claude API
key** gets `ai_enabled=1` once, so Glow, postmortems and the summary keep
working. This is logged as a warning and written to the audit log as
`settings.ai_enabled` by `system: upgrade (existing Claude API key)`. The marker
`ai_optin_migrated` makes sure it never runs again: if you switch AI off
afterwards, it stays off. A new installation has no key at that point and
starts with AI off. Disable it under Settings → AI if you did not want it.

Every later change of the switch is recorded with who and when
(`ai_enabled_by`, `ai_enabled_at`, audit actions `settings.ai_enabled` /
`settings.ai_disabled`); provider changes are audited as `settings.ai_update`
with the names of the changed settings, never the values.

### What is sent, and where

| Feature | Sent to the model |
|---|---|
| Glow | Host counts and names of offline hosts, up to 10 active incidents (severity, title, rule), syslog counts of the last hour with up to 3 example error messages, unhealthy integrations — plus the question and the last 10 chat messages |
| Postmortem | Incident title, rule, severity, times, event timeline, up to 5 syslog patterns (one example message each, up to 3 hostnames) |
| Daily summary | 24 h of incidents, down hosts, syslog error patterns, unhealthy integrations, expiring certificates |

The destination is the provider you choose:

| Provider | Base URL | Notes |
|---|---|---|
| Anthropic | — (api.anthropic.com) | API key; model defaults to `claude-haiku-4-5-20251001` |
| Azure OpenAI | `https://<resource>.openai.azure.com` | Set the **API version** (e.g. `2024-10-21`); the model field is the **deployment name**; API key required. Pick a resource in the region you need (e.g. Switzerland North) |
| Ollama | `http://<host>:11434/v1` | No key needed; model e.g. `llama3.1:8b` |
| vLLM / LM Studio / other OpenAI-compatible | `http://<host>:8000/v1`, `http://<host>:1234/v1` | Key optional |

Keys are stored encrypted with the installation's `SECRET_KEY`, like every
other credential, and are never returned by the API. **Test connection** sends a
fixed "Reply with OK" prompt (no infrastructure data), so it works before AI is
enabled.

The base URL may point at `localhost` or a private (RFC1918/ULA) address —
local models are a primary use case and only admins can set it. Cloud metadata
endpoints, link-local, multicast, `0.0.0.0` and Nodeglow's own services (`db`,
`clickhouse`, `updater`, …) are refused, and redirects are not followed.
Note that `localhost` is the Nodeglow container itself; for a model on the
Docker host use the host's LAN address.

Only plain text completion is used (no tool/function calling), so every
provider supports every feature. Token cost in the usage card is estimated for
Anthropic only.

### Redaction

On by default. Before anything is sent, Nodeglow replaces with placeholders
such as `<IP_1>`, `<USER_2>`, `<SECRET_1>`:

- IPv4 and IPv6 addresses (not `0.0.0.0` / loopback), MAC addresses
- e-mail addresses
- usernames in common log formats: sshd (`Failed password for X`,
  `Invalid user X`, `Accepted publickey for X`), PAM (`for user X`, `user=X`),
  sudo, Windows Security events (`Account Name:`, `DOMAIN\user`, SIDs,
  `Workstation Name:`), nginx/Apache access logs (remote user), home directories
- secrets: `password=` / `token=` / `api_key:` and similar, `Bearer …`,
  credentials in URLs, Anthropic/OpenAI/AWS/GitHub/Slack/JWT token formats
- optionally hostnames and FQDNs, including the names of monitored hosts and
  integrations (Settings → AI → "Also redact hostnames")

The same value gets the same placeholder throughout one request (context, your
question and the chat history), so the model can still correlate. A value found
once is replaced wherever else it appears in that request. Built-in accounts
such as `root` or `admin` are kept, because they are not personal data and
matter for the analysis. With "Show real values in answers" on, placeholders in
the answer are mapped back inside Nodeglow; the mapping itself is never sent.
Secrets are never mapped back.

**Redaction is best effort, not a guarantee.** It recognises the patterns above;
a name in an incident title, free text in a log message, a username in a format
it does not know or a short hostname that is not in the inventory passes
through. If data must not leave your network at all, use a local model
(Ollama, vLLM, LM Studio) or keep AI off.

### Settings keys

`ai_enabled`, `ai_enabled_by`, `ai_enabled_at`, `ai_provider`
(`anthropic` | `openai_compatible`), `claude_api_key`, `ai_anthropic_model`,
`ai_openai_base_url`, `ai_openai_api_key`, `ai_openai_model`,
`ai_openai_api_version`, `ai_redact_enabled`, `ai_redact_hostnames`,
`ai_restore_placeholders`, `ai_optin_migrated`. They live in the `settings`
table and are changed through the UI (`POST /settings/ai/save`).

---

## Configuration reference

Required in `.env` — the stack refuses to start without them:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password |
| `UPDATE_SIDECAR_TOKEN` | Shared secret between backend and updater. Generate with `openssl rand -hex 32` |

Useful optional settings:

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_SHARED_BUFFERS` | `512MB` | Raise on hosts with plenty of RAM |
| `POSTGRES_WORK_MEM` | `8MB` | Per-operation sort/hash memory |
| `SECRET_KEY` | unset | Encryption key. Strongly recommended — see [The encryption key](#the-encryption-key) |
| `BACKUP_SCHEDULE` | `02:30` | Scheduled dumps: `HH:MM` (UTC), `every 6h`, or `off` |
| `BACKUP_RETENTION` | `5` | Dumps to keep, per kind (scheduled / pre-update) |
| `DB_CONTAINER` | auto | Only needed if the database container cannot be resolved from the compose project (e.g. `vigil-db-1`) |
| `HOST_PROJECT_DIR` | auto | Host directory the stack runs from; only if the updater cannot read it from the compose labels ([Updater: host paths](#updater-host-paths)) |
| `UI_BIND` | `0.0.0.0` | Host address for the UI port 8000 |
| `SYSLOG_BIND` | `0.0.0.0` | Host address for the syslog ports 514/udp, 1514/tcp |
| `NODEGLOW_NO_NEW_PRIVILEGES` | `false` | `true` enables no-new-privileges for the backend; verify ICMP checks afterwards |
| `LOG_LEVEL` | `INFO` | Backend log level |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line |
| `APP_VERSION` | from `VERSION` | Build arg; set automatically by the UI update |
| `SKIP_MIGRATIONS` | unset | `1` skips the schema check on start — emergencies only |

Retention is configured in the UI under Settings, not through the environment:
integration snapshots (7 days), incident events (30 days) and log templates
(90 days).

---

## Security notes

**The updater sidecar is host-root-equivalent.** It holds the Docker socket and
can rebuild the stack, so anyone who reaches it with the token can execute code
as root on the host. It is exposed only on the internal Docker network — never
publish its port, and treat `UPDATE_SIDECAR_TOKEN` like a root password.

**Run behind TLS.** The application sets HSTS, CSP and frame-denial headers, but
it does not terminate TLS itself; put a reverse proxy in front of it. The full
list is the [hardening checklist](#hardening-checklist).
