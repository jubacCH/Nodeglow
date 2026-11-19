# Bundled Go binaries (cosign, docker CLI, compose, buildx) are scanned by the release
# Trivy gate; keep them on releases built with a current Go toolchain.
# cosign verifies release image signatures in image update mode
# (NODEGLOW_UPDATE_MODE=image, see image_update.py). Taken from the official,
# multi-arch Sigstore image at a pinned version; bump deliberately.
FROM gcr.io/projectsigstore/cosign:v3.1.4 AS cosign

# ── Release deploy files ─────────────────────────────────────────────────────
# The release workflow passes a named build context "deploy" holding
# deploy/docker-compose.yml (= docker-compose.release.yml) and
# deploy/clickhouse/*, which replaces this stage. The image-mode updater copies
# them into the installation directory on update, so a release can change its
# compose file and the files are covered by the image signature. A plain
# `docker build sidecar/` (git-mode installs) gets this empty stage instead.
FROM python:3.15-slim AS deploy
RUN mkdir -p /deploy

FROM python:3.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-29.8.1.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname -m)" \
       -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
    && curl -fsSL "https://github.com/docker/buildx/releases/download/v0.38.2/buildx-v0.38.2.linux-${ARCH}" \
       -o /usr/local/lib/docker/cli-plugins/docker-buildx \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# buildx: the backend image uses a named build context (ee/), which the
# classic builder rejects. Fail the image build, not the first update.
RUN docker buildx version >/dev/null

COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign
RUN cosign version >/dev/null

# NOTE: Runs as root — required for Docker socket access and git operations.
# The sidecar is not exposed to external traffic (internal network only).

WORKDIR /app
COPY update-server.py orchestrator.py image_update.py ./
COPY --from=deploy /deploy /app/deploy

EXPOSE 9100
CMD ["python", "update-server.py"]
