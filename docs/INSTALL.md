# Installing Nodeglow

This guide is for running a **released** version of Nodeglow: signed images
from the GitHub Container Registry, no build on your host. It covers online and
air-gapped installation, upgrades, backups and how to verify what you install.

Running from a git checkout instead (development, or the project's own
production) is described in the [README](../README.md#run-from-source) and
[OPERATIONS.md](OPERATIONS.md#updating). Day-2 operation — sizing, ports,
hardening, backups, the encryption key, diagnosis — is in
[OPERATIONS.md](OPERATIONS.md) and applies to both.

- [Editions and images](#editions-and-images)
- [Requirements](#requirements)
- [Install (online)](#install-online)
- [Install (offline / air-gapped)](#install-offline--air-gapped)
- [Upgrading](#upgrading)
- [Backup and restore](#backup-and-restore)
- [Verifying signatures](#verifying-signatures)
- [Configuration (.env)](#configuration-env)
- [Moving a git-based install to releases](#moving-a-git-based-install-to-releases)
- [Uninstalling](#uninstalling)
- [For maintainers: cutting a release](#for-maintainers-cutting-a-release)

---

## Editions and images

Every release publishes four images, each for `linux/amd64` and `linux/arm64`:

| Image | Contents | License |
|---|---|---|
| `ghcr.io/jubacch/nodeglow-backend` | backend **with** `ee/` (enterprise features) | AGPL-3.0 core + Nodeglow Enterprise License for `ee/` |
| `ghcr.io/jubacch/nodeglow-backend-community` | backend **without** `ee/` | AGPL-3.0-only |
| `ghcr.io/jubacch/nodeglow-frontend` | web UI (shared by both editions) | AGPL-3.0-only |
| `ghcr.io/jubacch/nodeglow-updater` | update and backup sidecar | AGPL-3.0-only |

Tags: `X.Y.Z` for every release, plus `X.Y` and `latest` for stable releases
(not for pre-releases such as `1.3.0-rc.1`). Installations always pin an exact
`X.Y.Z`; `latest` exists for trying things out.

**Which backend?** `nodeglow-backend` is the default. It is one image for
everybody: the enterprise features in it stay inactive until a valid license
key is installed (Settings → License or `NODEGLOW_LICENSE`, see
[OPERATIONS.md → Enterprise license](OPERATIONS.md#enterprise-license)), and
without one the image behaves like the community edition. We ship it this
way because the `ee/` source is public (source-available) anyway,
because customers can then start a trial or buy a license without changing
images, and because one tested image is easier to support than two diverging
ones. `nodeglow-backend-community` is built from the same commit without
`ee/`, for anyone who wants an install that contains AGPL code only — e.g.
for compliance reviews or redistribution. Switching later is a one-line change
in `.env` (`NODEGLOW_BACKEND_IMAGE`); the database is the same.

`NODEGLOW_DISABLE_EE=1` in `.env` turns `ee/` off in the default image as
well. See [LICENSING.md](../LICENSING.md) for the license terms.

The release images never contain the proprietary Ookla speedtest CLI
(`BUNDLE_OOKLA` is a build option for your own builds only).

---

## Requirements

- Linux host (ICMP checks need `NET_RAW`), x86_64 or arm64
- Docker Engine 20.10+ with the Compose v2 plugin (`docker compose`)
- 2 vCPU / 4 GB RAM / 20 GB SSD minimum — details in
  [OPERATIONS.md](OPERATIONS.md#system-requirements-and-sizing)
- `tar`, `gzip`, `sha256sum`, and `curl` or `wget` (online install)
- Optional but recommended: [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)
  — with it, `install.sh` also verifies the release signatures
- Ports: 8000/tcp (UI), 514/udp and 1514/tcp (syslog), see
  [Ports and firewall](OPERATIONS.md#ports-and-firewall)

---

## Install (online)

```sh
curl -fsSLO https://github.com/jubacCH/Nodeglow/releases/latest/download/install.sh
sudo sh install.sh                  # latest stable release into /opt/nodeglow
```

Options: `--version 1.2.0` (a specific release), `--dir /srv/nodeglow`,
`--edition community`, `--registry mirror.example.com/nodeglow` (a mirror),
`--require-signature` (fail unless cosign verifies everything), `--no-start`,
`--yes`. `sh install.sh --help` lists them all.

What it does:

1. checks the prerequisites (Docker, Compose v2, daemon access, disk, RAM);
2. downloads `nodeglow-X.Y.Z-deploy.tar.gz` (compose file, ClickHouse config)
   and `SHA256SUMS` from the GitHub Release, verifies the checksum and — if
   cosign is installed — the signature of `SHA256SUMS`;
3. writes `/opt/nodeglow/docker-compose.yml` and `clickhouse/`, and generates
   `.env` (mode 600) with fresh random `POSTGRES_PASSWORD`,
   `UPDATE_SIDECAR_TOKEN` and `SECRET_KEY`;
4. pulls the images (and verifies their cosign signatures if cosign is there);
5. starts the stack, waits for the backend to report healthy, and prints the
   URL.

**First login:** open `http://<host>:8000`. The setup wizard runs on the first
visit and creates the admin account — do it right away, until then anyone who
reaches port 8000 can claim the instance.

Then:

- **Back up `.env`** off the host. `SECRET_KEY` decrypts every stored
  credential and is in no database dump ([The encryption key](OPERATIONS.md#the-encryption-key)).
- Put a TLS reverse proxy in front and set `UI_BIND=127.0.0.1`
  ([Hardening checklist](OPERATIONS.md#hardening-checklist)).
- Arrange off-host copies of the database dumps ([Backups](OPERATIONS.md#backups)).

Prefer to see the files first? Download and unpack the deploy archive and run
the `install.sh` inside it; it uses the files next to it instead of
downloading them.

---

## Install (offline / air-gapped)

Each release attaches `nodeglow-X.Y.Z-offline-amd64.tar.gz` (when it fits the
release asset limit): every image the release needs (`docker save` of the
Nodeglow images with the default backend, plus the pinned Postgres and
ClickHouse), the compose file, the ClickHouse config, `install.sh`, a
`SHA256SUMS` of its contents and `VERIFY.md`.

Other platforms or the community edition: build the bundle on any machine with
Docker and internet access:

```sh
git clone https://github.com/jubacCH/Nodeglow.git && cd Nodeglow
git checkout v1.2.0
sh scripts/make-offline-bundle.sh --version 1.2.0 --platform linux/arm64 --edition community
# → dist/nodeglow-1.2.0-offline-arm64.tar.gz (+ its SHA-256 printed)
```

With cosign installed the script verifies the image signatures before saving
them (`--require-signature` makes that mandatory).

**1. Verify before transfer** (on the connected machine), see
[Verifying signatures](#release-files-installsh-archives-bundle):

```sh
cosign verify-blob --certificate SHA256SUMS.pem --signature SHA256SUMS.sig \
  --certificate-identity-regexp '^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
```

**2. Transfer** the bundle, and ideally `SHA256SUMS`, `SHA256SUMS.sig` and
`SHA256SUMS.pem` with it, to the target.

**3. Install:**

```sh
tar -xzf nodeglow-1.2.0-offline-amd64.tar.gz --strip-components=1 \
  nodeglow-1.2.0-offline-amd64/install.sh     # or use any copy of install.sh
sudo sh install.sh --offline nodeglow-1.2.0-offline-amd64.tar.gz
```

`install.sh --offline` checks every file in the bundle against its internal
`SHA256SUMS`, the bundle itself against the release's `SHA256SUMS` if that file
lies next to it (and the signature, if cosign is installed — note that keyless
verification of the signature needs to reach Sigstore, so on a fully isolated
host it is step 1 that counts), loads the images with `docker load` and starts
the stack. It sets `NODEGLOW_RELEASES_URL=off`: an air-gapped installation
does not look for updates.

**Private registry instead of a bundle:** mirror the images including their
signatures (`cosign copy ghcr.io/jubacch/nodeglow-backend:1.2.0
registry.example/nodeglow/nodeglow-backend:1.2.0`, same for the others) and
install with `--registry registry.example/nodeglow`. Signatures copied with
`cosign copy` still verify, because they are bound to the image digest, not
the registry.

---

## Upgrading

Releases follow semantic versioning. Database migrations are **forward-only**:
neither the updater nor `install.sh` installs an older version over a newer
one (`install.sh --force` overrides it — only together with restoring the
matching database dump).

### From the UI (online installs)

**Administration → System status → Software updates** lists newer releases
(stable only, unless `NODEGLOW_UPDATE_CHANNEL=prerelease`). **Update now**
runs, observable step by step:

| Step | What happens | If it fails |
|---|---|---|
| `preflight` | Docker socket, disk space, database running, `/data` unchanged, `.env` pins a version | Nothing changed |
| `resolve` | Picks the newest release above the running one | Nothing changed |
| `verify` | `cosign verify` of the backend, frontend and updater image: signed by this repository's release workflow for a tag | Nothing changed — **fail closed** |
| `backup` | `pg_dump` into the `backups` volume | Nothing changed |
| `pull` | Pulls the new images; their digests must equal the verified ones | Old containers keep serving |
| `stage` | Copies the release's compose file and ClickHouse config out of the new (verified) updater image | Old containers keep serving |
| `install` | Puts them in place and sets `NODEGLOW_VERSION` in `.env`; previous files kept as `*.bak-<run>` | Files restored |
| `migrate` | Applies migrations with the new backend image | Files restored, old containers keep serving |
| `restart` | Recreates backend and frontend | Shown as failed; the backup is listed |
| `handoff` | A short-lived helper recreates the updater itself on the new version | Reported; run `docker compose up -d updater` |

`NODEGLOW_VERIFY_SIGNATURES=0` disables the signature check (for images you
re-built yourself); without it a missing or invalid signature always stops the
update.

### With install.sh (online or offline)

Run `install.sh` again — it detects the existing installation:

```sh
cd /opt/nodeglow
sudo sh install.sh --version 1.3.0                     # online
sudo sh install.sh --offline nodeglow-1.3.0-offline-amd64.tar.gz   # air-gapped
```

It dumps the database to `/opt/nodeglow/backups/pre-upgrade-<old>-<time>.dump.gz`
first, keeps `.env` (only `NODEGLOW_VERSION` changes), replaces the compose file
and ClickHouse config (old copies kept as `*.bak-<time>`), loads or pulls the
images and recreates the containers. The backend applies migrations on start
and refuses to start if they fail.

### Customising the compose file

`docker-compose.yml` in the installation directory belongs to the release and
is replaced on every upgrade. Put local changes (resource limits, extra
networks, labels) into `.env` where a variable exists, or into a
`docker-compose.override.yml`, which `docker compose` merges automatically for
manual commands and `install.sh`. The UI updater does **not** read the
override file: if you depend on one, upgrade with `install.sh`.

---

## Backup and restore

Everything in [OPERATIONS.md → Backups](OPERATIONS.md#backups) applies: the
updater dumps Postgres daily at 02:30 UTC and before every update into the
`backups` volume, keeping 5 of each kind. In addition `install.sh` writes its
pre-upgrade dumps into `<install dir>/backups/`.

A complete backup of a release installation is:

| What | Where |
|---|---|
| Database dumps | `backups` volume (`docker volume ls \| grep backups`) and `<install dir>/backups/` |
| `.env` — contains `SECRET_KEY` | `<install dir>/.env` |
| `data/` (GeoIP files, file-based key if `SECRET_KEY` was ever unset) | `<install dir>/data/` |

ClickHouse (time series) is not backed up; losing it loses history, not
configuration.

**Restore onto a fresh host.** Postgres takes its password from `.env` when
its volume is first created, so put the saved `.env` in place *before* the
first start:

```sh
sudo sh install.sh --version 1.2.0 --no-start     # the version the dump came from, or newer
cd /opt/nodeglow
cp /safe/place/.env .env                          # your saved .env (SECRET_KEY!)
cp -a /safe/place/data/. data/                    # if you saved data/
docker compose up -d db
gunzip -c backup.dump.gz | docker compose exec -T db \
  pg_restore -U nodeglow -d nodeglow --clean --if-exists
docker compose up -d
```

If you are restoring into an installation that already ran, stop the app first
(`docker compose stop nodeglow frontend`) and restore as above. The backend
applies outstanding migrations on start, so a dump from an older version is
fine. Check afterwards that the backend log line
`Encryption key fingerprint: …` matches the fingerprint you escrowed.

---

## Verifying signatures

Everything a release publishes is signed **keylessly** with
[Sigstore cosign](https://docs.sigstore.dev/) by the release workflow. There
is no long-lived key to trust or to leak: the signing certificate names the
workflow and the tag that produced the artifact, and is logged in the public
Rekor transparency log. The identity to check is

```
issuer:   https://token.actions.githubusercontent.com
identity: https://github.com/jubacCH/Nodeglow/.github/workflows/release.yml@refs/tags/vX.Y.Z
```

### Images

```sh
cosign verify ghcr.io/jubacch/nodeglow-backend:1.2.0 \
  --certificate-identity-regexp '^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

To pin the exact tag, use `--certificate-identity
https://github.com/jubacCH/Nodeglow/.github/workflows/release.yml@refs/tags/v1.2.0`.

The images also carry a GitHub build-provenance attestation:

```sh
gh attestation verify oci://ghcr.io/jubacch/nodeglow-backend:1.2.0 --repo jubacCH/Nodeglow
```

### SBOM and provenance

Every image has an SPDX SBOM and SLSA provenance (BuildKit, `mode=max`)
attached:

```sh
docker buildx imagetools inspect ghcr.io/jubacch/nodeglow-backend:1.2.0 --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/jubacch/nodeglow-backend:1.2.0 --format '{{ json .Provenance }}'
```

### Release files (install.sh, archives, bundle)

All release assets are listed in `SHA256SUMS`, which is signed
(`SHA256SUMS.sig`, certificate `SHA256SUMS.pem`, and the same as a Sigstore
bundle in `SHA256SUMS.sigstore.json`):

```sh
cosign verify-blob --certificate SHA256SUMS.pem --signature SHA256SUMS.sig \
  --certificate-identity-regexp '^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
```

**Verify `install.sh` this way before running it as root**, if your policy
requires it — `curl | sh` is convenient, not auditable.

---

## Configuration (.env)

`install.sh` writes `<install dir>/.env` (mode 600) with:

- `NODEGLOW_VERSION` (the installed release; updates change it),
  `NODEGLOW_BACKEND_IMAGE` (edition), `NODEGLOW_REGISTRY`, `HOST_PROJECT_DIR`;
- fresh random `POSTGRES_PASSWORD`, `UPDATE_SIDECAR_TOKEN` and `SECRET_KEY` —
  **back up `.env`**;
- `NODEGLOW_UPDATE_CHANNEL=stable`, `NODEGLOW_RELEASES_URL` (`off` for offline
  installs) and `NODEGLOW_VERIFY_SIGNATURES=1`;
- commented examples for `UI_BIND`, `SYSLOG_BIND`, `BACKUP_SCHEDULE` and
  `BACKUP_RETENTION`.

What each variable does, its default, and the advanced ones that need a
`docker-compose.override.yml`: **[OPERATIONS.md → Configuration
reference](OPERATIONS.md#configuration-reference)**. After editing `.env`, run
`docker compose up -d` in the installation directory.

---

## Moving a git-based install to releases

A checkout-based installation (built with `docker-compose.yml`, updater in git
mode) keeps working and keeps updating from `main`. To switch it to releases:

1. Take a dump and save `.env` and `data/`
   ([Backups](OPERATIONS.md#backups)); note the encryption key fingerprint.
2. `sudo sh install.sh --version X.Y.Z --dir /opt/nodeglow --no-start` into a
   **new** directory, choosing the release that matches (or is newer than) the
   commit you run.
3. Copy `POSTGRES_*`, `UPDATE_SIDECAR_TOKEN`, `SECRET_KEY` and any settings
   from the old `.env` into the new one (keep the new `NODEGLOW_*` lines), and
   copy the old `data/` directory over.
4. Stop the old stack (`docker compose down` in the old directory — volumes
   stay), start the new one's database, restore the dump, then
   `docker compose up -d`. The volumes of the new installation have a new
   compose project name, which is why the restore is needed.

---

## Uninstalling

```sh
cd /opt/nodeglow
docker compose down          # keeps the volumes (database, ClickHouse, backups)
docker compose down -v       # ALSO deletes all data — irreversible
```

Then remove the directory.

---

## For maintainers: cutting a release

1. Make sure `main` is green and the `[Unreleased]` section of
   [CHANGELOG.md](../CHANGELOG.md) describes the release.
2. Run the helper — it bumps `VERSION`, turns `[Unreleased]` into
   `[X.Y.Z] - <date>`, updates the compare links, commits `release: vX.Y.Z`
   and creates the annotated tag. It does **not** push.

   ```sh
   scripts/release.sh 1.2.0        # or 1.3.0-rc.1 for a pre-release
   git show --stat HEAD
   git push origin main && git push origin v1.2.0
   ```

3. The tag starts [`.github/workflows/release.yml`](../.github/workflows/release.yml):
   `prepare` (VERSION must match the tag; notes from the changelog) → `test`
   (the whole CI workflow on the tag) → `images` (Trivy gate on CRITICAL
   fixable findings, multi-arch build and push with SBOM + provenance, cosign
   signature, build attestation) → `bundle` (offline bundle, verified
   signatures) → `release` (GitHub Release with all assets and signed
   `SHA256SUMS`). Pre-releases get no `X.Y`/`latest` tags and are marked as
   pre-release.
4. **First release only:** GHCR creates the four packages as *private*. Set
   each to *Public* (Package settings → Change visibility) so installations can
   pull without credentials; the link to the repository is set from the image
   labels.

A failed run can be re-run from the Actions tab; re-running replaces the
assets of an existing GitHub Release. To build a release by hand
(workflow_dispatch), start the workflow **on the tag** ("Use workflow from →
Tags"). Findings the Trivy gate should accept go into `.trivyignore`, each with
a comment saying why.

The agent binaries attached to a release are the ones `agent-build.yml`
committed to `backend/static/` — the same files the backend image serves to
agents for auto-update.
