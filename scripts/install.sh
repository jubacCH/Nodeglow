#!/bin/sh
# Nodeglow installer — install or upgrade a release installation.
#
#   Online (pulls signed images from GHCR):
#     curl -fsSLO https://github.com/jubacCH/Nodeglow/releases/latest/download/install.sh
#     sudo sh install.sh                      # latest release
#     sudo sh install.sh --version 1.2.0      # a specific release
#
#   Offline / air-gapped (images from a bundle, no network needed):
#     sudo sh install.sh --offline nodeglow-1.2.0-offline-amd64.tar.gz
#
# Running it again on an existing installation upgrades it: the database is
# dumped first, .env is kept (only NODEGLOW_VERSION changes), the release's
# compose file and ClickHouse config are replaced.
#
# Full documentation: docs/INSTALL.md. POSIX sh; keep it shellcheck-clean.

set -eu

REPO_SLUG="${NODEGLOW_REPO:-jubacCH/Nodeglow}"
DEFAULT_REGISTRY="ghcr.io/jubacch"
CERT_IDENTITY_RE='^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$'
OIDC_ISSUER="https://token.actions.githubusercontent.com"

INSTALL_DIR="/opt/nodeglow"
VERSION=""
EDITION=""
OFFLINE_BUNDLE=""
REGISTRY=""
VERIFY="auto"        # auto | require | skip
ASSUME_YES=0
NO_START=0
FORCE=0

WORK_DIR=""
SRC_DIR=""
UPGRADE=0
CURRENT_VERSION=""

# ── Output ───────────────────────────────────────────────────────────────────

if [ -t 1 ]; then
    C_BOLD=$(printf '\033[1m'); C_RED=$(printf '\033[31m'); C_YEL=$(printf '\033[33m')
    C_GRN=$(printf '\033[32m'); C_OFF=$(printf '\033[0m')
else
    C_BOLD=""; C_RED=""; C_YEL=""; C_GRN=""; C_OFF=""
fi

info() { printf '%s==>%s %s\n' "$C_BOLD" "$C_OFF" "$*"; }
ok()   { printf '%s ok%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%swarning:%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: install.sh [options]

  --version X.Y.Z        release to install (default: the latest release)
  --dir PATH             installation directory (default: /opt/nodeglow)
  --edition NAME         enterprise (default; enterprise features need a
                         license key, otherwise it runs as community) or
                         community (pure AGPL image without ee/)
  --offline BUNDLE       install from an offline bundle (.tar.gz), no network
  --registry HOST/ORG    pull from a mirror instead of ghcr.io/jubacch
  --require-signature    fail unless cosign can verify the release signatures
  --skip-signature       do not verify signatures even if cosign is installed
  --force                allow installing an older version over a newer one
  --no-start             prepare everything, do not start the stack
  -y, --yes              do not ask for confirmation
  -h, --help             this help
EOF
}

# ── Helpers ──────────────────────────────────────────────────────────────────

have() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}

confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    if [ ! -t 0 ]; then
        # Piped into sh: no terminal to ask. Proceed, the summary was printed.
        return 0
    fi
    printf '%s [y/N] ' "$1"
    read -r answer || answer=""
    case "$answer" in
        y|Y|yes|YES) return 0 ;;
        *) die "aborted" ;;
    esac
}

random_hex() {
    # $1 = number of random bytes
    if have openssl; then
        openssl rand -hex "$1"
    else
        od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'
    fi
}

sha256_of() {
    if have sha256sum; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# check_sum SUMS_FILE DIR NAME — the line for NAME in SUMS_FILE must match DIR/NAME
check_sum() {
    expected=$(awk -v n="$3" '{f=$2; sub(/^\*/, "", f); if (f == n) print $1}' "$1" | head -n1)
    [ -n "$expected" ] || die "$3 is not listed in $(basename "$1")"
    actual=$(sha256_of "$2/$3")
    [ "$expected" = "$actual" ] || die "checksum mismatch for $3 (expected $expected, got $actual)"
}

# check_all_sums DIR — every line of DIR/SHA256SUMS must match
check_all_sums() {
    [ -f "$1/SHA256SUMS" ] || die "no SHA256SUMS in $1"
    if have sha256sum; then
        (cd "$1" && sha256sum -c --quiet SHA256SUMS) || die "bundle contents do not match SHA256SUMS"
    else
        (cd "$1" && shasum -a 256 -c --quiet SHA256SUMS) || die "bundle contents do not match SHA256SUMS"
    fi
}

fetch() {
    # fetch URL DEST
    if have curl; then
        curl -fsSL --retry 3 -o "$2" "$1"
    elif have wget; then
        wget -q -O "$2" "$1"
    else
        die "need curl or wget to download $1"
    fi
}

# version_ge A B — true if A >= B (X.Y.Z, a -suffix sorts below the release)
version_ge() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        na = split(a, pa, "-"); nb = split(b, pb, "-")
        split(pa[1], x, "."); split(pb[1], y, ".")
        for (i = 1; i <= 3; i++) {
            if (x[i] + 0 > y[i] + 0) exit 0
            if (x[i] + 0 < y[i] + 0) exit 1
        }
        if (na == 1 && nb > 1) exit 0
        if (na > 1 && nb == 1) exit 1
        exit (pa[2] >= pb[2]) ? 0 : 1
    }'
}

env_get() {
    # env_get FILE KEY — last assignment wins, quotes stripped
    [ -f "$1" ] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$2=//p" "$1" | tail -n1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

env_set() {
    # env_set FILE KEY VALUE — replace (deduplicated) or append, keep the rest
    tmp="$1.tmp.$$"
    awk -v k="$2" -v v="$3" '
        BEGIN { done = 0 }
        {
            line = $0; sub(/^[ \t]*export[ \t]+/, "", line)
            if (index(line, k "=") == 1) {
                if (!done) { print k "=" v; done = 1 }
                next
            }
            print
        }
        END { if (!done) print k "=" v }
    ' "$1" > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$1"
}

dc() {
    docker compose --project-directory "$INSTALL_DIR" -f "$INSTALL_DIR/docker-compose.yml" "$@"
}

# ── Steps ────────────────────────────────────────────────────────────────────

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION=${2#v}; shift 2 ;;
            --dir) [ $# -ge 2 ] || die "--dir needs a value"; INSTALL_DIR=$2; shift 2 ;;
            --edition) [ $# -ge 2 ] || die "--edition needs a value"; EDITION=$2; shift 2 ;;
            --offline) [ $# -ge 2 ] || die "--offline needs a bundle"; OFFLINE_BUNDLE=$2; shift 2 ;;
            --registry) [ $# -ge 2 ] || die "--registry needs a value"; REGISTRY=$2; shift 2 ;;
            --require-signature) VERIFY=require; shift ;;
            --skip-signature) VERIFY=skip; shift ;;
            --force) FORCE=1; shift ;;
            --no-start) NO_START=1; shift ;;
            -y|--yes) ASSUME_YES=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) usage >&2; die "unknown option: $1" ;;
        esac
    done
    case "$EDITION" in
        ""|enterprise|community) ;;
        *) die "--edition must be enterprise or community" ;;
    esac
    case "$INSTALL_DIR" in
        /*) ;;
        *) INSTALL_DIR="$(pwd)/$INSTALL_DIR" ;;
    esac
    INSTALL_DIR=${INSTALL_DIR%/}
    if [ -n "$VERSION" ]; then
        printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' \
            || die "--version must look like 1.2.3 (got $VERSION)"
    fi
    if [ -n "$OFFLINE_BUNDLE" ] && [ -n "$REGISTRY" ]; then
        die "--registry and --offline exclude each other (bundles carry their images)"
    fi
}

check_prereqs() {
    info "Checking prerequisites"
    [ "$(uname -s)" = "Linux" ] || warn "not Linux: ICMP checks need a Linux Docker host (NET_RAW)"
    have docker || die "docker is not installed — https://docs.docker.com/engine/install/"
    docker compose version >/dev/null 2>&1 \
        || die "the Docker Compose v2 plugin is missing ('docker compose')"
    docker info >/dev/null 2>&1 \
        || die "cannot talk to the Docker daemon — run as root or as a member of the docker group"
    for tool in tar gzip awk sed; do
        have "$tool" || die "$tool is required"
    done
    have sha256sum || have shasum || die "sha256sum (coreutils) or shasum is required"
    have openssl || [ -r /dev/urandom ] || die "need openssl or /dev/urandom to generate secrets"
    if [ -z "$OFFLINE_BUNDLE" ]; then
        have curl || have wget || die "curl or wget is required (or use --offline)"
    fi
    if [ -r /proc/meminfo ]; then
        mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
        [ "${mem_kb:-0}" -ge 3500000 ] || warn "less than 4 GB RAM; see docs/OPERATIONS.md for sizing"
    fi
    parent=$INSTALL_DIR
    while [ ! -d "$parent" ]; do parent=$(dirname "$parent"); done
    [ -w "$parent" ] || die "$parent is not writable — run as root or choose --dir"
    free_kb=$(df -Pk "$parent" | awk 'NR == 2 {print $4}')
    [ "${free_kb:-0}" -ge 5000000 ] || warn "less than 5 GB free under $parent"
    if [ "$VERIFY" = require ] && ! have cosign; then
        die "--require-signature: cosign is not installed (https://docs.sigstore.dev/cosign/system_config/installation/)"
    fi
    ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), compose $(docker compose version --short 2>/dev/null || echo '?')"
}

can_verify() {
    case "$VERIFY" in
        skip) return 1 ;;
        require) return 0 ;;
        *) have cosign ;;
    esac
}

verify_sums_signature() {
    # verify_sums_signature DIR — DIR holds SHA256SUMS(.sig|.pem)
    if ! can_verify; then
        warn "cosign not installed: release signature NOT verified (checksums still are)"
        return 0
    fi
    if [ ! -f "$1/SHA256SUMS.sig" ] || [ ! -f "$1/SHA256SUMS.pem" ]; then
        [ "$VERIFY" = require ] && die "SHA256SUMS.sig / SHA256SUMS.pem not found next to SHA256SUMS"
        warn "no signature files next to SHA256SUMS: signature NOT verified"
        return 0
    fi
    cosign verify-blob \
        --certificate "$1/SHA256SUMS.pem" --signature "$1/SHA256SUMS.sig" \
        --certificate-identity-regexp "$CERT_IDENTITY_RE" \
        --certificate-oidc-issuer "$OIDC_ISSUER" \
        "$1/SHA256SUMS" >/dev/null 2>&1 \
        || die "SHA256SUMS signature verification FAILED — do not install this release"
    ok "SHA256SUMS signed by the Nodeglow release workflow"
}

is_deploy_dir() {
    [ -f "$1/VERSION" ] && [ -f "$1/docker-compose.yml" ] && [ -f "$1/clickhouse/config.xml" ] \
        && [ -f "$1/clickhouse/init.sql" ]
}

latest_version() {
    fetch "https://api.github.com/repos/$REPO_SLUG/releases/latest" "$WORK_DIR/latest.json" \
        || die "could not ask GitHub for the latest release; pass --version"
    tag=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK_DIR/latest.json" | head -n1)
    [ -n "$tag" ] || die "no release found for $REPO_SLUG"
    printf '%s' "${tag#v}"
}

obtain_online() {
    [ -n "$VERSION" ] || VERSION=$(latest_version)
    base="https://github.com/$REPO_SLUG/releases/download/v$VERSION"
    archive="nodeglow-$VERSION-deploy.tar.gz"
    info "Downloading Nodeglow $VERSION"
    mkdir -p "$WORK_DIR/dl"
    fetch "$base/$archive" "$WORK_DIR/dl/$archive" || die "release v$VERSION not found ($base)"
    fetch "$base/SHA256SUMS" "$WORK_DIR/dl/SHA256SUMS" || die "SHA256SUMS missing in release v$VERSION"
    fetch "$base/SHA256SUMS.sig" "$WORK_DIR/dl/SHA256SUMS.sig" 2>/dev/null || rm -f "$WORK_DIR/dl/SHA256SUMS.sig"
    fetch "$base/SHA256SUMS.pem" "$WORK_DIR/dl/SHA256SUMS.pem" 2>/dev/null || rm -f "$WORK_DIR/dl/SHA256SUMS.pem"
    verify_sums_signature "$WORK_DIR/dl"
    check_sum "$WORK_DIR/dl/SHA256SUMS" "$WORK_DIR/dl" "$archive"
    ok "checksum of $archive"
    tar -xzf "$WORK_DIR/dl/$archive" -C "$WORK_DIR"
    SRC_DIR="$WORK_DIR/nodeglow-$VERSION"
    is_deploy_dir "$SRC_DIR" || die "$archive does not contain a deploy directory"
}

obtain_offline() {
    [ -f "$OFFLINE_BUNDLE" ] || die "bundle not found: $OFFLINE_BUNDLE"
    bundle_dir=$(CDPATH='' cd -- "$(dirname -- "$OFFLINE_BUNDLE")" && pwd)
    bundle_name=$(basename -- "$OFFLINE_BUNDLE")
    info "Checking offline bundle $bundle_name"
    # Optional: the release's signed SHA256SUMS next to the bundle.
    if [ -f "$bundle_dir/SHA256SUMS" ] && grep -q " \*\{0,1\}$bundle_name\$" "$bundle_dir/SHA256SUMS"; then
        verify_sums_signature "$bundle_dir"
        check_sum "$bundle_dir/SHA256SUMS" "$bundle_dir" "$bundle_name"
        ok "bundle checksum matches the release's SHA256SUMS"
    elif [ "$VERIFY" = require ]; then
        die "--require-signature: put the release's SHA256SUMS, .sig and .pem next to the bundle"
    else
        warn "no release SHA256SUMS next to the bundle: verify it before transfer (docs/INSTALL.md)"
    fi
    tar -xzf "$OFFLINE_BUNDLE" -C "$WORK_DIR"
    SRC_DIR=$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d -name 'nodeglow-*-offline-*' | head -n1)
    [ -n "$SRC_DIR" ] && is_deploy_dir "$SRC_DIR" && [ -f "$SRC_DIR/images.tar" ] \
        || die "$bundle_name is not a Nodeglow offline bundle"
    check_all_sums "$SRC_DIR"
    ok "bundle contents match their checksums"
    bundle_version=$(head -n1 "$SRC_DIR/VERSION")
    if [ -n "$VERSION" ] && [ "$VERSION" != "$bundle_version" ]; then
        die "--version $VERSION but the bundle holds $bundle_version"
    fi
    VERSION=$bundle_version
    bundle_backend=$(cat "$SRC_DIR/BACKEND_IMAGE" 2>/dev/null || echo nodeglow-backend)
    case "$EDITION" in
        community) want=nodeglow-backend-community ;;
        enterprise) want=nodeglow-backend ;;
        *) want=$bundle_backend ;;
    esac
    [ "$want" = "$bundle_backend" ] \
        || die "this bundle carries $bundle_backend; build one for --edition $EDITION (scripts/make-offline-bundle.sh)"
    EDITION_IMAGE=$bundle_backend
}

detect_existing() {
    if [ -f "$INSTALL_DIR/.env" ]; then
        UPGRADE=1
        CURRENT_VERSION=$(env_get "$INSTALL_DIR/.env" NODEGLOW_VERSION)
        [ -n "$CURRENT_VERSION" ] \
            || die "$INSTALL_DIR/.env has no NODEGLOW_VERSION — this looks like a git-based install; see docs/INSTALL.md (migrating)"
        if [ "$FORCE" != 1 ] && ! version_ge "$VERSION" "$CURRENT_VERSION"; then
            die "installed is $CURRENT_VERSION, refusing to downgrade to $VERSION (database migrations are forward-only; --force to override)"
        fi
    fi
}

backup_before_upgrade() {
    if ! dc ps --status running -q db 2>/dev/null | grep -q .; then
        warn "database container not running: no pre-upgrade dump taken"
        return 0
    fi
    mkdir -p "$INSTALL_DIR/backups"
    stamp=$(date -u +%Y-%m-%dT%H-%M-%S)
    dump="$INSTALL_DIR/backups/pre-upgrade-$CURRENT_VERSION-$stamp.dump.gz"
    db_user=$(env_get "$INSTALL_DIR/.env" POSTGRES_USER); db_user=${db_user:-nodeglow}
    db_name=$(env_get "$INSTALL_DIR/.env" POSTGRES_DB); db_name=${db_name:-nodeglow}
    info "Dumping the database to $dump"
    failed="$WORK_DIR/dump.failed"
    { dc exec -T db pg_dump -U "$db_user" -Fc "$db_name" || : > "$failed"; } | gzip > "$dump"
    if [ -f "$failed" ] || ! gzip -t "$dump" 2>/dev/null; then
        rm -f "$dump"
        die "pg_dump failed — nothing was changed"
    fi
    chmod 600 "$dump"
    ok "pre-upgrade dump ($(du -h "$dump" | awk '{print $1}'))"
}

install_files() {
    mkdir -p "$INSTALL_DIR/clickhouse" "$INSTALL_DIR/data"
    if [ "$UPGRADE" = 1 ]; then
        stamp=$(date -u +%Y%m%dT%H%M%S)
        for f in docker-compose.yml clickhouse/config.xml clickhouse/init.sql; do
            [ -f "$INSTALL_DIR/$f" ] && cp -p "$INSTALL_DIR/$f" "$INSTALL_DIR/$f.bak-$stamp"
        done
        cp -p "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.bak-$stamp"
    fi
    cp "$SRC_DIR/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
    cp "$SRC_DIR/clickhouse/config.xml" "$INSTALL_DIR/clickhouse/config.xml"
    cp "$SRC_DIR/clickhouse/init.sql" "$INSTALL_DIR/clickhouse/init.sql"
    cp "$SRC_DIR/install.sh" "$INSTALL_DIR/install.sh" 2>/dev/null || true
    printf '%s\n' "$VERSION" > "$INSTALL_DIR/VERSION"
}

write_env() {
    env_file="$INSTALL_DIR/.env"
    if [ "$UPGRADE" = 1 ]; then
        env_set "$env_file" NODEGLOW_VERSION "$VERSION"
        [ -n "$(env_get "$env_file" HOST_PROJECT_DIR)" ] || env_set "$env_file" HOST_PROJECT_DIR "$INSTALL_DIR"
        [ -n "$REGISTRY" ] && env_set "$env_file" NODEGLOW_REGISTRY "$REGISTRY"
        [ -n "$EDITION_IMAGE" ] && env_set "$env_file" NODEGLOW_BACKEND_IMAGE "$EDITION_IMAGE"
        [ -n "$OFFLINE_BUNDLE" ] && env_set "$env_file" NODEGLOW_RELEASES_URL off
        ok "kept .env, NODEGLOW_VERSION=$VERSION"
        return 0
    fi
    releases_url=""
    [ -z "$OFFLINE_BUNDLE" ] || releases_url=off
    old_umask=$(umask)
    umask 077
    cat > "$env_file" <<EOF
# Nodeglow — generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Keep a copy of this file somewhere safe: SECRET_KEY decrypts every stored
# credential and is NOT part of any database backup (docs/INSTALL.md).

# Release to run. The updater changes this when it installs a new release.
NODEGLOW_VERSION=$VERSION
# nodeglow-backend (enterprise features, need a license) or
# nodeglow-backend-community (pure AGPL).
NODEGLOW_BACKEND_IMAGE=${EDITION_IMAGE:-nodeglow-backend}
NODEGLOW_REGISTRY=${REGISTRY:-$DEFAULT_REGISTRY}
# Host directory of this installation (the updater needs it).
HOST_PROJECT_DIR=$INSTALL_DIR

POSTGRES_DB=nodeglow
POSTGRES_USER=nodeglow
POSTGRES_PASSWORD=$(random_hex 24)
UPDATE_SIDECAR_TOKEN=$(random_hex 32)
SECRET_KEY=$(random_hex 32)

# Updates: stable | prerelease. "off" disables release checks (air-gapped).
NODEGLOW_UPDATE_CHANNEL=stable
NODEGLOW_RELEASES_URL=$releases_url
# Verify cosign signatures before the updater installs a release (fail closed).
NODEGLOW_VERIFY_SIGNATURES=1

# Network exposure: 127.0.0.1 when a reverse proxy on this host terminates TLS.
# UI_BIND=0.0.0.0
# SYSLOG_BIND=0.0.0.0

# Backups: daily pg_dump at 02:30 UTC into the "backups" volume.
# BACKUP_SCHEDULE=02:30
# BACKUP_RETENTION=5

# LOG_LEVEL=INFO
# LOG_FORMAT=text
EOF
    umask "$old_umask"
    chmod 600 "$env_file"
    ok "generated $env_file with fresh secrets"
}

load_or_pull_images() {
    if [ -n "$OFFLINE_BUNDLE" ]; then
        info "Loading images from the bundle (this takes a while)"
        docker load -i "$SRC_DIR/images.tar" >/dev/null
        ok "images loaded: $(wc -l < "$SRC_DIR/images.txt" | tr -d ' ')"
        return 0
    fi
    info "Pulling images"
    dc pull --quiet
    if can_verify; then
        registry=$(env_get "$INSTALL_DIR/.env" NODEGLOW_REGISTRY); registry=${registry:-$DEFAULT_REGISTRY}
        backend=$(env_get "$INSTALL_DIR/.env" NODEGLOW_BACKEND_IMAGE); backend=${backend:-nodeglow-backend}
        for image in "$backend" nodeglow-frontend nodeglow-updater; do
            cosign verify "$registry/$image:$VERSION" \
                --certificate-identity-regexp "$CERT_IDENTITY_RE" \
                --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1 \
                || die "signature verification FAILED for $registry/$image:$VERSION"
        done
        ok "image signatures verified"
    fi
}

start_stack() {
    info "Starting Nodeglow $VERSION"
    dc up -d --remove-orphans
    printf 'waiting for the backend to become healthy '
    i=0
    while [ $i -lt 90 ]; do
        status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' nodeglow 2>/dev/null || echo missing)
        case "$status" in
            healthy) printf '\n'; ok "backend healthy"; return 0 ;;
            exited|dead) printf '\n'; dc logs --tail 50 nodeglow >&2 || true; die "the backend container stopped (logs above)" ;;
        esac
        printf '.'
        sleep 2
        i=$((i + 1))
    done
    printf '\n'
    warn "backend not healthy after 3 minutes — check: docker compose -f $INSTALL_DIR/docker-compose.yml logs nodeglow"
}

print_summary() {
    ui_bind=$(env_get "$INSTALL_DIR/.env" UI_BIND)
    case "${ui_bind:-0.0.0.0}" in
        0.0.0.0|"")
            host=$(hostname -I 2>/dev/null | awk '{print $1}')
            host=${host:-localhost} ;;
        *) host=$ui_bind ;;
    esac
    what=installed
    [ "$UPGRADE" != 1 ] || what=upgraded
    cat <<EOF

${C_BOLD}Nodeglow $VERSION is $what in $INSTALL_DIR${C_OFF}

  Open:        http://$host:8000
EOF
    if [ "$UPGRADE" = 1 ]; then
        cat <<EOF
  Upgraded:    $CURRENT_VERSION -> $VERSION (database dump in $INSTALL_DIR/backups/)
EOF
    else
        cat <<EOF
  First login: the setup wizard opens on the first visit and creates the
               admin account. Do this right away — until then anyone who can
               reach port 8000 can claim the instance.
EOF
    fi
    cat <<EOF

  Next steps:
    * Back up $INSTALL_DIR/.env (SECRET_KEY!) somewhere off this host.
    * Put TLS in front (reverse proxy) and set UI_BIND=127.0.0.1 in .env.
    * Copy the database dumps off the host: docs/OPERATIONS.md#backups
    * Manage:   cd $INSTALL_DIR && docker compose ps | logs | down
    * Updates:  System -> Status -> Software Updates in the UI, or run
                sh $INSTALL_DIR/install.sh again (--offline BUNDLE when air-gapped).

EOF
}

main() {
    parse_args "$@"
    check_prereqs
    WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/nodeglow-install.XXXXXX")
    trap cleanup EXIT
    trap 'exit 130' INT TERM
    EDITION_IMAGE=""
    case "$EDITION" in
        community) EDITION_IMAGE=nodeglow-backend-community ;;
        enterprise) EDITION_IMAGE=nodeglow-backend ;;
    esac

    script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || echo "")
    if [ -n "$OFFLINE_BUNDLE" ]; then
        obtain_offline
    elif [ -z "$VERSION" ] && [ -n "$script_dir" ] && [ "$script_dir" != "$INSTALL_DIR" ] \
            && is_deploy_dir "$script_dir"; then
        # Run from an extracted deploy archive: use its files.
        SRC_DIR=$script_dir
        VERSION=$(head -n1 "$SRC_DIR/VERSION")
        info "Using the deploy files next to this script (version $VERSION)"
    else
        obtain_online
    fi

    detect_existing
    if [ "$UPGRADE" = 1 ]; then
        info "Upgrading $CURRENT_VERSION -> $VERSION in $INSTALL_DIR"
    else
        info "Installing $VERSION in $INSTALL_DIR"
    fi
    confirm "Continue?"

    mkdir -p "$INSTALL_DIR"
    if [ "$UPGRADE" = 1 ]; then
        backup_before_upgrade
    fi
    install_files
    write_env
    dc config --quiet || die "the compose file does not render with $INSTALL_DIR/.env"
    load_or_pull_images
    if [ "$NO_START" = 1 ]; then
        ok "prepared; start with: cd $INSTALL_DIR && docker compose up -d"
        return 0
    fi
    start_stack
    print_summary
}

# NODEGLOW_INSTALL_SOURCED=1 loads the functions without running (tests).
[ "${NODEGLOW_INSTALL_SOURCED:-0}" = 1 ] || main "$@"
