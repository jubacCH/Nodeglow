# Operating Nodeglow

What you need to run this in production: updating, backing up, restoring, and
working out what is wrong when something looks off.

Installing a release: [INSTALL.md](INSTALL.md). Running from a git checkout:
[README → Run from source](../README.md#run-from-source). Everything below
applies to both unless a section says otherwise. All environment variables
are listed in the [Configuration reference](#configuration-reference).

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
- A git-mode update needs **2 GB free** (preflight check) for the image build.
- **Practical ceiling: roughly 1000 monitored hosts** at the default 60 s
  interval. Checks run 50 at a time, and an unreachable host holds its slot
  for the full timeout (2 s ping, 3 s TCP, 5 s HTTP), so many hosts down at
  once stretches a round towards the interval. Scheduled jobs are
  `max_instances=1`: a round that overruns is skipped, not stacked, and the
  self-check raises an incident when a job stops completing.
- Scaling out is not supported out of the box. `REDIS_URL` makes the rate
  limiter multi-process safe; scheduler leader election across several
  backend processes (only one runs the jobs) is an enterprise feature
  (`ee/`, licensed). Without it the backend runs single-instance and logs a
  warning when `REDIS_URL` is set. The syslog listener and the shipped compose
  files assume a single backend either way, and they do not pass `REDIS_URL`
  to the backend — see [Advanced backend settings](#advanced-backend-settings).
- Edition: the image built by the shipped compose file includes the
  enterprise features in `ee/` (see [LICENSING.md](../LICENSING.md)).
  `NODEGLOW_DISABLE_EE=1` runs the community edition; the log line
  `Nodeglow edition: ...` at startup and `GET /api/v2/features` show which
  one is running. The enterprise features need a license key; without one
  they stay inactive and everything else works (see
  [Enterprise license](#enterprise-license)).

### Ports and firewall

| Port | Proto | Direction | Purpose | Expose to |
|---|---|---|---|---|
| 8000 | tcp | inbound | Web UI and API (frontend, proxies to the backend) | Users and agents; put TLS in front |
| 514 | udp | inbound | Syslog (mapped to container 1514) | Devices that send logs |
| 1514 | tcp | inbound | Syslog over TCP | Devices that send logs |
| 514 (configurable) | udp + tcp | inbound **on a probe host** | [Probe syslog relay](#probe-syslog-relay), only where switched on | Devices in the probe's network |
| 8000 (backend), 5432, 8123, 9100 | tcp | internal only | Backend, Postgres, ClickHouse, updater | Docker network only — never publish |
| varies | icmp/tcp/udp | outbound | Checks, integrations (HTTPS APIs), SNMP 161/udp | Monitored networks |
| 443 | tcp | outbound | Notifications (Telegram, Discord, Teams, Slack, ntfy, webhooks), the AI provider if AI is enabled, GeoIP updates, GitHub / GHCR for updates | Internet |
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
- [ ] The app runs as its own database user with row-level security on
      (`POSTGRES_APP_USER`, `NODEGLOW_RLS=on`; new installs have it, older
      ones: [Switching an existing install on](#switching-an-existing-install-on)).

---

## Updating

There are two kinds of installation, and the updater sidecar handles each in
its own mode (`NODEGLOW_UPDATE_MODE`, set by the compose file):

| Installation | Compose file | Updater mode | Updates to |
|---|---|---|---|
| **Release install** — customers, on-prem, air-gapped; made by `install.sh` | `docker-compose.release.yml` (copied as `docker-compose.yml`) | `image` | the newest signed **release** from GHCR |
| **Git checkout** — development, the project's own production | `docker-compose.yml` | `git` (default) | `origin/main`, built on the host |

Release installs are documented in **[INSTALL.md](INSTALL.md#upgrading)**:
the UI update there verifies the cosign signatures (fail closed), dumps the
database, pulls, installs the release's compose file, migrates and restarts;
air-gapped installs upgrade with `install.sh --offline <bundle>`. The rest of
this section describes the git mode.

### From the UI

**Administration → System status → Software updates → Update now.**

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
other directory on the host (e.g. `/opt/nodeglow`). Compose turns relative bind
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

- **Two channels.** Git installs follow `main`: the updater only ever
  fast-forwards to `origin/main`. Release installs follow tagged releases
  (`vX.Y.Z`, cut with `scripts/release.sh`, built and signed by
  `.github/workflows/release.yml`); `VERSION` and the tag must agree, and
  [CHANGELOG.md](../CHANGELOG.md) is the release note. Pre-releases
  (`X.Y.Z-rc.N`) are offered only with `NODEGLOW_UPDATE_CHANNEL=prerelease`.
- **Updates are forward-only.** Migrations have no tested downgrade path. The
  way back from a bad update is restoring the `pre-update-*` dump together with
  the previous commit (`git checkout <old sha>` + rebuild) — or, for a release
  install, the previous `NODEGLOW_VERSION` (the updater keeps the old `.env`
  and compose file as `*.bak-<run>`) — not a downgrade migration.
- **Update regularly rather than in big jumps.** Every migration runs on every
  deploy path, so skipping releases works, but a small step is easier to
  diagnose if something fails.
- **Before updating:** check that the last scheduled backup is recent
  (`docker compose logs updater | grep "scheduled backup"`), and read the
  commit list (git mode) or the [changelog](../CHANGELOG.md) (releases) for
  anything marked as needing an `.env` change.
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

The JSON export (`POST /api/v1/backup` with `{"passphrase": "…"}`) records the
fingerprint of the key it was made with; a JSON restore under a different key
logs an error and returns a `warning` field.

The export contains password hashes, API key hashes, settings and the stored
credentials, so it is always encrypted with a passphrase chosen at export time
(at least 12 characters; scrypt + AES-256-GCM). Keep the passphrase with the
escrowed `SECRET_KEY` — without it the file cannot be restored. Restore with
`POST /api/v1/backup/restore` and `{"backup": <file>, "passphrase": "…"}`.
Unencrypted exports from older versions are still accepted, but only with
`"allow_unencrypted": true` in the body.

### Tenant keys (envelope encryption)

Since revision 046 the credentials of a tenant (integration configs, SNMP /
WinRM / SSH credentials, notification channel secrets, SMTP and phpIPAM
passwords) are encrypted with that **tenant's own data key**. The data keys
are random, stored only wrapped (encrypted) by a key derived from
`SECRET_KEY` in the table `tenant_keys`. Installation secrets (LDAP bind
password, AI provider keys, licence, GeoIP key) keep the `SECRET_KEY`
directly. Nothing changes for escrow: `SECRET_KEY` still opens everything,
and a `pg_dump` contains the wrapped keys.

- The migration 046 rewrites no credential: the old backend keeps serving
  while the updater migrates, and it can read only the old format. The new
  backend moves every credential to the tenant key when it starts, and does
  so again on every start for anything written in the old format later.
  Values that do not decrypt with the current `SECRET_KEY` are left exactly
  as they are.
- **Going back to an older version after the new one has started** needs
  the downgrade first, while the new version still runs:
  `docker compose exec nodeglow python -m alembic downgrade 044` (back to
  2.0.x; this also undoes 047 and 045: the probe syslog relay settings,
  support grants and tenant lifecycle columns are dropped). It puts every
  credential back under `SECRET_KEY`. Switching `NODEGLOW_VERSION` back alone
  leaves the credentials under tenant keys, which the old version cannot
  read (integrations fail, notifications do not go out). Restoring the
  updater's pre-update dump works as well.
- Check the state at any time (read-only, prints counts, never a secret;
  exit code 1 when something does not decrypt):

  ```bash
  docker compose exec nodeglow python -m services.tenant_keys verify
  ```

  `upgrade` moves remaining old-format values, `rotate --tenant N` gives a
  tenant a new data key and re-encrypts its secrets (the old key stays
  readable as "retired").
- Purging a deleted tenant (ee, after its 30 days) destroys its data keys
  first, before any row is deleted: its credentials are then unreadable in the database, replicas and every dump or export taken
  afterwards, even with `SECRET_KEY` (crypto-shred). A dump or JSON export
  taken **before** the deletion contains the wrapped keys along with the
  data, so it stays readable with `SECRET_KEY` until it is deleted: keep
  backup retention within what you promise customers for deleted data.
- The JSON export decrypts nothing. It carries the credentials as stored
  and the `tenant_keys` rows (data keys wrapped by `SECRET_KEY`), so opening
  a credential from it needs the escrowed `SECRET_KEY` as well as the
  passphrase. Holding an operator API key and choosing a passphrase is not
  enough. A restore into an installation with a different `SECRET_KEY`
  warns, and its credentials do not decrypt until the original key is
  configured. To enter credentials again there instead, give each tenant a
  new key with `rotate --tenant N`.
- A backup holds no `tenants` table. Rows of a tenant that the target
  installation does not have are restored as they are, and no key is
  created for that tenant. The restore result names those tenants in
  `warning`.

### Changing it

There is no re-encryption tooling for `SECRET_KEY` itself. Setting a different
`SECRET_KEY` makes all existing credentials unreadable (the wrapped tenant keys
included); they then have to be entered again. Treat the key as permanent for
the life of the installation.

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

## Row-level security

Every customer's rows carry a `tenant_id`, and two independent layers keep
them apart. The application filter (always on) adds the tenant to every ORM
query and refuses queries without one. PostgreSQL **row-level security**
(RLS) is the net under it: a policy on every tenant-owned table lets a
connection see and write only the rows of the tenant the application set
for the current transaction — also for raw SQL and for any query the filter
missed. A connection that set no tenant sees nothing.

| | New install (`install.sh`, from 2026-10) | Existing install |
|---|---|---|
| Database users | `POSTGRES_USER` owns the schema and migrates; the app runs as `nodeglow_app` | one user for everything, as before |
| `NODEGLOW_RLS` | `on` | `off` — nothing changes |
| What the migrations did | created `nodeglow_app`, its grants, the policies, RLS on | created `nodeglow_app` (cannot log in), its grants and the policies; RLS stays off |

`migrate.py` (run on every start and by every update) brings the database in
line with `.env`: it creates the app role and sets its password from the app
URL, grants it SELECT/INSERT/UPDATE/DELETE on every table, keeps a policy
`tenant_isolation` on every tenant-owned table, and enables + forces RLS when
`NODEGLOW_RLS=on` — or disables it again when it is `off`. The app role owns
nothing and cannot `TRUNCATE`, change the schema or switch RLS off; the
backup restore therefore uses the owner connection.

### Switching an existing install on

Do it in two steps, so a problem is easy to place. Take a backup first
([Taking one manually](#taking-one-manually)).

1. **Separate app user, RLS still off.** Add to `.env`:

   ```bash
   POSTGRES_APP_USER=nodeglow_app
   POSTGRES_APP_PASSWORD=<openssl rand -hex 24>
   NODEGLOW_RLS=off
   ```

   then `docker compose up -d nodeglow`. The start (migrate.py, as
   `POSTGRES_USER`) lets `nodeglow_app` log in with that password; the app
   now connects as it. Check that the UI works as before and that the log
   shows `app login nodeglow_app` and no `nodeglow.rls` warning:

   ```bash
   docker compose logs nodeglow | grep -E "row-level security|nodeglow.rls"
   ```

2. **RLS on**, after a few days without surprises: set `NODEGLOW_RLS=on`,
   `docker compose up -d nodeglow`. The log says
   `row-level security enabled and forced on N tenant tables` (migrate) and
   `row-level security active on N tenant tables (app role nodeglow_app)`
   (backend). In the database:

   ```bash
   docker compose exec db psql -U nodeglow -c "SELECT relname, relforcerowsecurity FROM pg_class WHERE relrowsecurity"
   ```

**Back out** at any point: set `NODEGLOW_RLS=off` in `.env` and run
`docker compose up -d nodeglow` (RLS is switched off again, the policies stay
and do nothing); to return to one user remove `POSTGRES_APP_USER` /
`POSTGRES_APP_PASSWORD` as well. A plain `docker compose restart` keeps the
container's old environment and changes nothing.

Apply changes to these variables with `docker compose up -d nodeglow`. An
update started from the UI does not switch RLS **on** in its migrate step
(the old backend is still serving then); the new backend switches it on when
it starts.

Turning RLS on while the app still connects as `POSTGRES_USER` protects
nothing in the Compose stack: that user is a superuser, and superusers bypass
RLS. The backend logs a warning saying so.

### What the backend checks at start

| Log (`nodeglow.rls`) | Meaning |
|---|---|
| `... NODEGLOW_RLS is off: the app sets no tenant and those tables look empty` (error) | RLS is enabled in the database but the flag is off — e.g. `SKIP_MIGRATIONS=1` after switching. Set the flag back or run migrate.py |
| `... enabled/forced on only x/y of N tenant tables — run migrate.py` (error) | Flag on, database not (yet) in line |
| `... connects as <user>, which is a superuser / BYPASSRLS` (warning) | RLS cannot apply to this connection; use the app user |
| `cannot create role nodeglow_app (needs CREATEROLE)` (warning, migrate) | The owner may not manage roles (managed PostgreSQL). Not fatal: have a DBA run `CREATE ROLE nodeglow_app LOGIN PASSWORD '...' NOBYPASSRLS;` and restart; migrate.py does the grants |

### Limits

- RLS is defence in depth against a *forgotten tenant predicate*, not against
  SQL injection: cross-tenant work (token lookups, installation jobs) sets a
  system bypass (`nodeglow.system`) that the app role can set itself.
- The owner's password is in the backend container's environment, because
  the container runs the migrations before the app starts.
- A dump restored into a **new** PostgreSQL cluster references `nodeglow_app`
  in its grants; `pg_restore` reports those statements as errors when the
  role does not exist yet. Harmless — the next start creates the role and
  the grants.

---

## Probe syslog relay

Syslog from network devices normally goes straight to the server (514/udp,
1514/tcp). When the devices cannot reach the server — a remote site, a
customer network, a hosted (SaaS) installation — a **probe** can receive it
instead: the probe listens for syslog inside that network and forwards the
lines over its own agent connection (HTTPS to the UI port, nothing inbound on
the server). Every relayed message belongs to the probe's tenant and site; the
sending device's address travels with each message and is matched against the
hosts **of that site**.

### Setting it up

1. Enrol an agent in the network the devices are in and make it a probe
   (**Infrastructure → Agents & Probes**, or `PATCH /api/v1/agents/{id}` with
   `{"is_probe": true}`).
2. Switch the relay on (admin):
   `PATCH /api/v1/agents/{id}` with `{"syslog_relay_enabled": true}`, and
   optionally `"syslog_relay_port": 1514` (default 514, the same port for UDP
   and TCP). The probe picks it up with its next heartbeat (≤ its report
   interval) and starts listening on all interfaces of the probe host.
3. Point the devices' syslog at the probe's address and port. UDP (one message
   per datagram) and TCP (newline-terminated or octet-counted, RFC 6587) both
   work; at most 64 KiB per message, 64 TCP connections.
4. Check `GET /api/v1/agents/{id}` → `syslog_relay.status`: `listening`,
   `received`, `forwarded` (stored by the backend), `rejected` (refused by the
   backend: not on the allowlist, invalid, oversized), `dropped` (lost in the
   probe's buffer), `buffered`, `send_failures` and, if the port could not be
   opened, `error`. Without step 1 the backend drops what a probe relays
   (`relay_disabled`), whatever the probe does.

Ports below 1024 need privileges on Linux: the agent installed by the install
script runs as root and can use 514. An agent running as an ordinary user
reports `cannot listen on port 514: permission denied` in `status.error` and
retries every minute — use a port ≥ 1024 then. Open the port in the probe
host's firewall for the devices (Windows: an inbound rule for the agent's
executable or the port).

While the server is unreachable the probe keeps listening and buffers up to
20 000 messages / 16 MiB in memory, then drops the oldest (counted in
`dropped`); a restart of the agent loses what is buffered. Switching the relay
off (or the probe role) closes the port within one heartbeat.

### How messages are attributed

- Tenant and site come from the probe's token. Nothing in a message — its
  host name, any field the probe sends — can move it into another tenant.
- The device's host is looked up among the hosts of the probe's site first
  (host name, then address). Outside the site only a **host name** or a
  **public address** is matched, and only when exactly one site of the tenant
  knows it: `192.168.1.1` at branch A is never branch B's `192.168.1.1`.
  Reverse DNS is not used for relayed addresses — the server's resolver knows
  nothing about the customer's network.
- The per-tenant "only accept syslog from known hosts" setting applies with
  the same site rule.
- An agent that is not a probe cannot relay; it only sends its own logs, which
  are attributed to its own host by name.

### The direct listener

`NODEGLOW_SYSLOG_LISTENER` (backend environment, see [Advanced backend
settings](#advanced-backend-settings)) controls the server's own syslog ports:

| Value | Behaviour |
|---|---|
| `auto` (default) | Listens. Messages go to the default tenant as long as the installation has **one** tenant — every community install, nothing changes. With more than one tenant it refuses messages (it cannot tell whose a datagram is), logs a warning every five minutes and counts them as `nodeglow_syslog_messages_dropped_total{reason="listener_disabled"}`; use probe relays. Until the server has counted its tenants once (the database unreachable at startup) it refuses as well. |
| `on` | Listens and always files messages under the default tenant. |
| `off` | Does not open the ports at all. Hosted (SaaS) installations: syslog is probe-only. Agent logs and probe relays keep working. |

With `off` you can also remove the 514/1514 port mappings from the compose
file.

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

## Enterprise license

The enterprise features in `ee/` (HA scheduler, Glow, AI postmortems, the AI
daily summary) are switched on by a license key: a signed file, verified
offline on the server (no phone-home, works air-gapped). Monitoring never
depends on it.

| License state | What happens |
|---|---|
| none installed / unusable | Enterprise features inactive: their endpoints answer `402`, their jobs skip, the UI hides them. Everything else works. |
| valid | The features listed in the license work. |
| expired, first 14 days | Still working (grace period); admins see a banner. |
| expired, after 14 days | Monitoring keeps running and nothing is deleted. Enterprise actions are refused (`402`), data they produced stays visible (e.g. stored postmortems). HA leader election keeps running so replicas do not duplicate jobs. |

### Installing a license

**In the UI (recommended):** Settings → License → paste the license key →
*Install license*. Admins only; the change is recorded in the audit log
(`license.install` / `license.remove`) and applies without a restart. The
key is stored in the `settings` table, so it is part of the database backup.

**Through the environment:** set `NODEGLOW_LICENSE` in `.env` to the key
itself or to a path inside the container, e.g. put the file into `./data/`
and set `NODEGLOW_LICENSE=/data/nodeglow-license.txt`, then
`docker compose up -d nodeglow`. The environment wins over a key installed in
the UI, and the settings page shows it read-only.

Check it: Settings → License, `GET /api/v2/features` (`license.status`), or
on the host

```bash
docker compose exec -w /opt/nodeglow-ee nodeglow python -m nodeglow_ee.licensing verify /data/nodeglow-license.txt
```

HA leader election is decided when the scheduler starts: after installing a
license that includes it, restart the backends once.

**Installation-bound licenses** (optional): Settings → License shows an
*Installation ID*. A license issued for that ID works only on this
installation. Licenses are not bound unless the issuer asks for it.

### Issuing licenses (vendor / owner)

License keys are signed with a private Ed25519 key that is **never** stored in
the repository or on a Nodeglow server; its public key is embedded in
`ee/backend/nodeglow_ee/licensing.py` (`TRUSTED_KEYS`, key id `ng-2026-10`).
From `ee/backend/`, on the machine that holds the private key:

```bash
# once: a key pair (prints the public key line for TRUSTED_KEYS)
python -m nodeglow_ee.licensing keygen --out-dir ~/nodeglow-license-keys --kid ng-2026-10

# a license (features: "all" or a comma list; omit --max-tenants for unlimited)
python -m nodeglow_ee.licensing issue \
  --signing-key ~/nodeglow-license-keys/ng-2026-10.private.pem --kid ng-2026-10 \
  --customer "ACME AG" --expires 2027-12-31 --features all --out acme-license.txt
```

The owner's own installation uses a license issued the same way — there is no
built-in bypass.

### Tenants (enterprise)

With the `multi_tenancy` feature an installation holds several customers
(tenants) whose data never meets. Installation operators manage them under
Administration → Tenants (`/tenants`); the license's `max_tenants` caps how
many exist (suspended and deleting ones count, purged ones do not).

- **The second tenant** is refused until the ClickHouse tenant rebuild has
  completed (System → Status, `clickhouse_tenant_rebuild`) and PostgreSQL
  row-level security protects the app's connection
  ([Row-level security](#row-level-security): `NODEGLOW_RLS=on`, the app as
  `nodeglow_app`). An install that knowingly runs without RLS sets
  `NODEGLOW_TENANTS_WITHOUT_RLS=1`; the application filter is then the only
  isolation layer.
- **Suspending** stops monitoring of the tenant and every login, API key,
  agent and install token of it; nothing is deleted. **Deleting** suspends
  the tenant for 30 days (cancellable), then a background job purges every
  PostgreSQL and ClickHouse row of it, its memberships and the users who
  belong to no other tenant (audit entry `tenant.purged`). The purge
  destroys the tenant's data keys first ([Tenant keys](#tenant-keys-envelope-encryption)). The default
  tenant (id 1) cannot be suspended or deleted.
- **Plans** (Standard 30 / Professional 90 / Enterprise 365 days) cap every
  retention setting of a tenant; its admins choose within the plan. A plan
  below the tenant's stored retention lowers it after a confirmation, and
  the nightly cleanup then deletes the older history for good. The default
  tenant gets no plan; "No plan" takes a tenant back to the installation
  caps.
- **Operators see no tenant data.** A tenant's admin grants time-limited
  support access under Settings → Support access; every access is in that
  tenant's audit log (`acting_via = support`). Support access cannot manage
  users, passwords, API keys or install tokens of the tenant, and never
  makes anyone an operator.
- **Users are global.** A tenant admin can reset the password only of users
  who belong to that tenant alone; for a user who also reaches another
  tenant, the provider or the installation, only the user or an operator
  can. The AI provider, its endpoint and keys (Settings → AI) are
  installation settings: only operators change them.
- When the license expires every tenant keeps being monitored and reachable;
  creating, suspending, resuming and plan changes are refused, cancelling a
  deletion and ending support access still work, scheduled purges still run. Back up the private key (password manager / vault); without
it no new licenses can be issued. Rotating the key: generate a new one with a
new `--kid`, add its public key to `TRUSTED_KEYS` next to the old one, and
remove the old id only once every license signed with it has been replaced.

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

This is the one list of environment variables; the other documents link
here. Everything in this first table goes into `.env` next to the compose file
and is read by both `docker-compose.yml` (git checkout) and
`docker-compose.release.yml` (release install) unless the last column says
otherwise. `install.sh` generates `.env` for release installs; for a checkout,
start from `.env.example`.

| Variable | Default | Purpose | Used by |
|---|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | `nodeglow` | Database name and the user that owns the schema and runs the migrations | both |
| `POSTGRES_APP_USER` | `POSTGRES_USER` | Database user the app runs as; `install.sh`: `nodeglow_app`. Created by the migrations ([Row-level security](#row-level-security)) | both |
| `POSTGRES_APP_PASSWORD` | `POSTGRES_PASSWORD` | Its password; `install.sh` generates it | both |
| `NODEGLOW_RLS` | `off` | `on` enables PostgreSQL row-level security on every tenant table; `install.sh` sets `on` ([Row-level security](#row-level-security)) | both |
| `CLICKHOUSE_PASSWORD` | `POSTGRES_PASSWORD` | Separate ClickHouse password, if wanted | both |
| `POSTGRES_SHARED_BUFFERS` | `512MB` | Raise on hosts with plenty of RAM, lower on small ones | both |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | `1536MB` | Planner hint; about half the host RAM | both |
| `POSTGRES_WORK_MEM` | `8MB` | Per-operation sort/hash memory | both |
| `POSTGRES_MAINTENANCE_WORK_MEM` | `128MB` | Memory for vacuum and index builds | both |
| `SECRET_KEY` | unset (file in `./data`) | Encryption key for stored credentials. Strongly recommended; `install.sh` generates it — see [The encryption key](#the-encryption-key) | both |
| `NODEGLOW_LICENSE` | unset | Enterprise license key or a path to a file with it ([Enterprise license](#enterprise-license)) | both |
| `NODEGLOW_DISABLE_EE` | unset | `1` runs the community edition even with `ee/` present | both |
| `NODEGLOW_TENANTS_WITHOUT_RLS` | unset | `1` lets the enterprise multi-tenancy create a second tenant without PostgreSQL row-level security ([Tenants](#tenants-enterprise)) | both |
| `NODEGLOW_ALLOW_SHARED_ENROLLMENT` | unset | `1` re-enables the legacy shared agent enrollment key; leave unset | both |
| `UI_BIND` | `0.0.0.0` | Host address for the UI port 8000 (`127.0.0.1` behind a local reverse proxy) | both |
| `SYSLOG_BIND` | `0.0.0.0` | Host address for the syslog ports 514/udp, 1514/tcp | both |
| `NODEGLOW_NO_NEW_PRIVILEGES` | `false` | `true` enables no-new-privileges for the backend; verify ICMP checks afterwards | both |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | both |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line | both |
| `SKIP_MIGRATIONS` | unset | `1` skips the schema upgrade on start — emergencies only | both |
| `BACKUP_SCHEDULE` | `02:30` | Scheduled dumps: `HH:MM` (UTC), `every 6h`, or `off` | both |
| `BACKUP_RETENTION` | `5` | Dumps to keep, per kind (scheduled / pre-update) | both |
| `DB_CONTAINER` | auto | Only if the database container cannot be resolved from the compose project (e.g. `nodeglow-db-1`) | both |
| `HOST_PROJECT_DIR` | auto | Host directory the stack runs from; only if the updater cannot read it from the compose labels ([Updater: host paths](#updater-host-paths)). `install.sh` sets it | both |
| `APP_VERSION` | from `VERSION` | Build arg: the version shown in UI and API; set automatically by the UI update | checkout |
| `BUNDLE_OOKLA` | `0` | Build arg: `1` bundles the proprietary Ookla speedtest CLI — own installations only, never redistribute | checkout |
| `NODEGLOW_VERSION` | — (required) | Image tag of all Nodeglow images; changed by updates | release |
| `NODEGLOW_BACKEND_IMAGE` | `nodeglow-backend` | `nodeglow-backend-community` for the AGPL-only edition ([Editions](INSTALL.md#editions-and-images)) | release |
| `NODEGLOW_REGISTRY` | `ghcr.io/jubacch` | Registry/namespace to pull from (mirror) | release |
| `NODEGLOW_UPDATE_CHANNEL` | `stable` | `prerelease` to be offered release candidates | release |
| `NODEGLOW_RELEASES_URL` | GitHub Releases API | `off` disables update checks (air-gapped) | release |
| `NODEGLOW_VERIFY_SIGNATURES` | `1` | `0` lets the updater install unsigned images (self-built mirrors only) | release |

`NODEGLOW_UPDATE_MODE` (`git` / `image`) is fixed by the compose file and is
not meant to be changed in `.env`.

### Advanced backend settings

The backend and the updater read a few more variables that the shipped compose
files do **not** pass through. To use one, add it to the service's
`environment` in a `docker-compose.override.yml` (merged automatically by
`docker compose` and `install.sh`; the UI updater in image mode does not read
it — see [INSTALL.md](INSTALL.md#customising-the-compose-file)).

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `REDIS_URL` | `nodeglow` | unset | Shared rate-limit state across processes; with an enterprise license also scheduler leader election (HA) |
| `DATABASE_URL`, `DATABASE_OWNER_URL` (alias `MIGRATION_DATABASE_URL`) | `nodeglow` | built from `POSTGRES_*` | The app's and the schema owner's connection. Compose builds `DATABASE_URL`; the owner is `POSTGRES_USER` on the bundled `db` only when `POSTGRES_APP_USER` is set, otherwise `DATABASE_URL`. **If you point `DATABASE_URL` at another server and use two users, set `DATABASE_OWNER_URL` too** — the backend logs an error when the two name different databases |
| `SECURE_COOKIES` | `nodeglow` | auto | `1` always marks session cookies `Secure` (otherwise only when the request or `X-Forwarded-Proto` is HTTPS) |
| `CORS_ORIGINS` | `nodeglow` | unset | Comma list of origins allowed to call the API cross-origin; not needed for the bundled UI |
| `DEBUG` | `nodeglow` | unset | `1` serves the OpenAPI docs at `/api/docs` — never in production |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` | `nodeglow` | unset | OpenTelemetry tracing export (OTLP/HTTP) |
| `NODEGLOW_INTEGRATION_CONCURRENCY` | `nodeglow` | `8` | Integrations collected in parallel |
| `NODEGLOW_INTEGRATION_TIMEOUT` | `nodeglow` | `60` | Seconds per integration collect |
| `NODEGLOW_SPEEDTEST_TIMEOUT` | `nodeglow` | `180` | Seconds per speedtest run |
| `NODEGLOW_PING_LOOKBACK_HOURS` | `nodeglow` | `24` | How far back the "latest check" lookup searches |
| `NODEGLOW_SYSLOG_LISTENER` | `nodeglow` | `auto` | The server's own syslog listener: `auto` (default tenant while there is one tenant), `on`, `off` (probe-only, SaaS) — [Probe syslog relay](#the-direct-listener) |
| `NODEGLOW_SYSLOG_CLOCK_SKEW_HOURS` | `nodeglow` | `24` | How far a sender's clock may lag and its messages still show up in time-window queries |
| `NODEGLOW_CLICKHOUSE_COMPRESSION` | `nodeglow` | `lz4` | ClickHouse client compression |
| `DNS_CACHE_TTL` | `nodeglow` | `60` | Seconds DNS lookups of check targets are cached; `0` disables |
| `LDAP_CA_CERTS_FILE` | `nodeglow` | unset | CA bundle for LDAPS when not set in the LDAP settings |
| `GEOIP_DB_PATH` | `nodeglow` | `/data/geoip/GeoLite2-City.mmdb` | Alternative GeoLite2 database file |
| `NODEGLOW_EE_PATH` | `nodeglow` | set by the image | Where the enterprise plugin source lives |
| `NODEGLOW_COSIGN_IDENTITY_REGEXP`, `NODEGLOW_COSIGN_OIDC_ISSUER` | `updater` | this repository's release workflow | Signing identity accepted in image mode — only for your own signed builds |

Retention is configured in the UI under Settings, not through the environment:
integration snapshots (7 days), incident events (30 days) and log templates
(90 days). Ping results and other ClickHouse time series expire by a fixed
TTL.

---

## Security notes

**The updater sidecar is host-root-equivalent.** It holds the Docker socket and
can rebuild the stack, so anyone who reaches it with the token can execute code
as root on the host. It is exposed only on the internal Docker network — never
publish its port, and treat `UPDATE_SIDECAR_TOKEN` like a root password.

**Run behind TLS.** The application sets HSTS, CSP and frame-denial headers, but
it does not terminate TLS itself; put a reverse proxy in front of it. The full
list is the [hardening checklist](#hardening-checklist).
