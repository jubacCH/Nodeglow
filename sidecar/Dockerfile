# cosign verifies release image signatures in image update mode
# (NODEGLOW_UPDATE_MODE=image, see image_update.py). Taken from the official,
# multi-arch Sigstore image at a pinned version; bump deliberately.
FROM gcr.io/projectsigstore/cosign:v2.4.1 AS cosign

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.5.1.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-$(uname -m)" \
       -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign
RUN cosign version >/dev/null

# NOTE: Runs as root — required for Docker socket access and git operations.
# The sidecar is not exposed to external traffic (internal network only).

WORKDIR /app
COPY update-server.py orchestrator.py image_update.py ./

EXPOSE 9100
CMD ["python", "update-server.py"]
