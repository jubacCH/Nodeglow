#!/bin/sh
# Build release archives for a Nodeglow version.
#
#   scripts/make-offline-bundle.sh --version 1.2.0 [--platform linux/amd64] \
#       [--edition enterprise|community] [--out dist]
#       → dist/nodeglow-1.2.0-offline-amd64.tar.gz   (air-gapped installs)
#
#   scripts/make-offline-bundle.sh --deploy-only --version 1.2.0 [--out dist]
#       → dist/nodeglow-1.2.0-deploy.tar.gz          (compose file + config,
#         what install.sh downloads; no images, no Docker needed)
#
# The offline bundle contains everything an installation without network
# access needs: `docker save` of all images (Nodeglow's and the pinned
# Postgres/ClickHouse), the release compose file, the ClickHouse config,
# install.sh, a SHA256SUMS of its contents and VERIFY.md. Install with
#   sh install.sh --offline nodeglow-1.2.0-offline-amd64.tar.gz
#
# Needs Docker (pull/save) unless --deploy-only. If cosign is installed, the
# Nodeglow images are signature-verified before they are saved; pass
# --require-signature to make that mandatory.
#
# POSIX sh; keep it shellcheck-clean.

set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")

VERSION=""
PLATFORM="linux/amd64"
EDITION="enterprise"
REGISTRY="ghcr.io/jubacch"
OUT_DIR="dist"
DEPLOY_ONLY=0
REQUIRE_SIG=0
CERT_IDENTITY_RE='^https://github\.com/jubacCH/Nodeglow/\.github/workflows/release\.yml@refs/tags/v.*$'
OIDC_ISSUER="https://token.actions.githubusercontent.com"

die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
}

sha256_line() {
    # sha256_line FILE (relative to cwd) → "<hash>  <file>"
    if have sha256sum; then
        sha256sum "$1"
    else
        shasum -a 256 "$1"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION=${2#v}; shift 2 ;;
        --platform) [ $# -ge 2 ] || die "--platform needs a value"; PLATFORM=$2; shift 2 ;;
        --edition) [ $# -ge 2 ] || die "--edition needs a value"; EDITION=$2; shift 2 ;;
        --registry) [ $# -ge 2 ] || die "--registry needs a value"; REGISTRY=$2; shift 2 ;;
        --out) [ $# -ge 2 ] || die "--out needs a value"; OUT_DIR=$2; shift 2 ;;
        --deploy-only) DEPLOY_ONLY=1; shift ;;
        --require-signature) REQUIRE_SIG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

[ -n "$VERSION" ] || VERSION=$(sed -n '/^[[:space:]]*[^#[:space:]]/{p;q;}' "$REPO_ROOT/VERSION" | tr -d '[:space:]')
printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' \
    || die "not a release version: $VERSION"
case "$EDITION" in
    enterprise) BACKEND_IMAGE=nodeglow-backend ;;
    community) BACKEND_IMAGE=nodeglow-backend-community ;;
    *) die "--edition must be enterprise or community" ;;
esac
ARCH=${PLATFORM#linux/}
ARCH=$(printf '%s' "$ARCH" | tr '/' '-')

mkdir -p "$OUT_DIR"
OUT_DIR=$(CDPATH='' cd -- "$OUT_DIR" && pwd)

# ── Deploy files (shared by both archives) ───────────────────────────────────

stage_deploy_files() {
    # stage_deploy_files DIR
    mkdir -p "$1/clickhouse"
    cp "$REPO_ROOT/docker-compose.release.yml" "$1/docker-compose.yml"
    cp "$REPO_ROOT/clickhouse/config.xml" "$REPO_ROOT/clickhouse/init.sql" "$1/clickhouse/"
    cp "$REPO_ROOT/scripts/install.sh" "$1/install.sh"
    chmod 755 "$1/install.sh"
    printf '%s\n' "$VERSION" > "$1/VERSION"
}

if [ "$DEPLOY_ONLY" = 1 ]; then
    name="nodeglow-$VERSION"
    rm -rf "${OUT_DIR:?}/$name"
    stage_deploy_files "$OUT_DIR/$name"
    tar -czf "$OUT_DIR/$name-deploy.tar.gz" -C "$OUT_DIR" "$name"
    info "wrote $OUT_DIR/$name-deploy.tar.gz (staged files kept in $OUT_DIR/$name)"
    exit 0
fi

# ── Offline bundle ───────────────────────────────────────────────────────────

have docker || die "docker is required to build an offline bundle"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
if [ "$REQUIRE_SIG" = 1 ] && ! have cosign; then
    die "--require-signature: cosign is not installed"
fi

name="nodeglow-$VERSION-offline-$ARCH"
stage="$OUT_DIR/$name"
rm -rf "${stage:?}"
stage_deploy_files "$stage"
printf '%s\n' "$BACKEND_IMAGE" > "$stage/BACKEND_IMAGE"

# The image list comes from the compose file itself, so the bundle can never
# miss an image the release needs. Dummy secrets satisfy the :? guards.
images=$(cd "$stage" && NODEGLOW_VERSION="$VERSION" NODEGLOW_REGISTRY="$REGISTRY" \
    NODEGLOW_BACKEND_IMAGE="$BACKEND_IMAGE" POSTGRES_PASSWORD=bundle UPDATE_SIDECAR_TOKEN=bundle \
    docker compose -f docker-compose.yml config --images | sort -u)
[ -n "$images" ] || die "docker compose config --images returned nothing"

info "Pulling images for $PLATFORM"
for image in $images; do
    docker pull --quiet --platform "$PLATFORM" "$image" >/dev/null
    printf '  %s\n' "$image"
done

if have cosign; then
    info "Verifying signatures of the Nodeglow images"
    for image in $images; do
        case "$image" in
            "$REGISTRY"/nodeglow-*)
                cosign verify "$image" \
                    --certificate-identity-regexp "$CERT_IDENTITY_RE" \
                    --certificate-oidc-issuer "$OIDC_ISSUER" >/dev/null 2>&1 \
                    || die "signature verification failed for $image"
                printf '  verified %s\n' "$image" ;;
        esac
    done
else
    printf 'warning: cosign not installed, image signatures NOT verified\n' >&2
fi

info "Saving images (this takes a while)"
# shellcheck disable=SC2086  # word splitting of the image list is intended
docker save -o "$stage/images.tar" $images
: > "$stage/images.txt"
for image in $images; do
    digest=$(docker image inspect --format '{{range .RepoDigests}}{{.}} {{end}}' "$image" | awk '{print $1}')
    printf '%s %s\n' "$image" "${digest:-unknown}" >> "$stage/images.txt"
done

cat > "$stage/VERIFY.md" <<EOF
# Verifying this bundle

Nodeglow $VERSION, offline bundle for $PLATFORM ($EDITION edition).

1. **Before transfer** (on a machine with internet access), check the bundle
   against the release's signed checksum list. Download SHA256SUMS and
   SHA256SUMS.sigstore.json from
   https://github.com/jubacCH/Nodeglow/releases/tag/v$VERSION and run:

       cosign verify-blob \\
         --bundle SHA256SUMS.sigstore.json \\
         --certificate-identity-regexp '$CERT_IDENTITY_RE' \\
         --certificate-oidc-issuer '$OIDC_ISSUER' \\
         SHA256SUMS
       sha256sum --ignore-missing -c SHA256SUMS

   Bundles you built yourself with scripts/make-offline-bundle.sh are not
   listed there; compare the SHA-256 the script printed instead.

2. **On the target**, install.sh checks every file in the bundle against the
   SHA256SUMS inside it, and — if you put the release's SHA256SUMS and
   SHA256SUMS.sigstore.json next to the bundle and cosign is installed — the
   signature as well:

       sh install.sh --offline $name.tar.gz

3. images.txt lists every image with the registry digest it was pulled at.
   The Nodeglow images were cosign-verified when the bundle was built (if
   cosign was available); see docs/INSTALL.md.
EOF

(cd "$stage" && {
    for f in VERSION BACKEND_IMAGE docker-compose.yml clickhouse/config.xml clickhouse/init.sql \
             install.sh images.tar images.txt VERIFY.md; do
        sha256_line "$f"
    done
} > SHA256SUMS)

info "Packing $name.tar.gz"
tar -czf "$OUT_DIR/$name.tar.gz" -C "$OUT_DIR" "$name"
rm -rf "${stage:?}"
size=$(du -h "$OUT_DIR/$name.tar.gz" | awk '{print $1}')
info "wrote $OUT_DIR/$name.tar.gz ($size)"
(cd "$OUT_DIR" && sha256_line "$name.tar.gz")
