# ── Build stage: install dependencies ────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# iputils-ping for ICMP checks, snmp for SNMP monitoring, gosu for privilege drop
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping snmp gosu libcap2-bin ca-certificates curl \
    && setcap cap_net_raw+ep /usr/bin/ping \
    && rm -rf /var/lib/apt/lists/*

# Ookla speedtest CLI — OFF by default, and deliberately so.
#
# The binary is proprietary. Ookla's licence restricts redistribution and wants
# a separate agreement for commercial use, so an image that is sold or handed to
# a customer must not carry it. Building with BUNDLE_OOKLA=1 is an opt-in the
# operator makes for their own installation, where personal use applies.
#
# Without it the integration falls back to the pip package speedtest-cli, which
# is unmaintained since 2021 and reports a fixed ping of 1800000 ms against some
# connections. That is why the operator may want the opt-in; it is not a reason
# to make it the default.
#
# Installed as `ookla-speedtest`, not `speedtest`: the pip package also ships a
# `speedtest` entry point, and `COPY --from=builder /install /usr/local` below
# would overwrite the binary with it. Tool detection would then find the old
# tool and invoke it with Ookla's arguments, breaking the integration outright.
ARG TARGETARCH=amd64
ARG OOKLA_VERSION=1.2.0
ARG BUNDLE_OOKLA=0
RUN set -eux; \
    if [ "$BUNDLE_OOKLA" != "1" ]; then \
        echo "BUNDLE_OOKLA is not 1 — shipping without the proprietary Ookla CLI"; \
        exit 0; \
    fi; \
    case "$TARGETARCH" in \
        amd64) ookla_arch=x86_64 ;; \
        arm64) ookla_arch=aarch64 ;; \
        arm) ookla_arch=armhf ;; \
        *) ookla_arch="" ;; \
    esac; \
    if [ -n "$ookla_arch" ]; then \
        curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_VERSION}-linux-${ookla_arch}.tgz" \
            | tar -xz -C /tmp speedtest \
        && mv /tmp/speedtest /usr/local/bin/ookla-speedtest \
        && chmod +x /usr/local/bin/ookla-speedtest \
        && /usr/local/bin/ookla-speedtest --version >/dev/null; \
    else \
        echo "No Ookla build for $TARGETARCH — falling back to speedtest-cli"; \
    fi

COPY --from=builder /install /usr/local

# Non-root user
RUN groupadd -r nodeglow && useradd -r -g nodeglow -d /app -s /sbin/nologin nodeglow

COPY . .
RUN chown -R nodeglow:nodeglow /app

# Embed build version (set via --build-arg or defaults to "dev")
ARG GIT_COMMIT=dev
ARG BUILD_DATE=unknown
RUN echo "{\"commit\":\"${GIT_COMMIT}\",\"date\":\"${BUILD_DATE}\"}" > /app/.build-version

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]

# Client IPs. Every browser request reaches this process through the Next.js
# frontend, so without proxy headers request.client is always the frontend
# container: the per-IP login throttle became one global counter (one client
# could lock everybody out) and the audit log recorded one address for all.
#
# --proxy-headers makes uvicorn take the client from X-Forwarded-For, but only
# when the direct peer is in FORWARDED_ALLOW_IPS (uvicorn reads this env var
# itself; CIDRs allowed). The default trusts Docker's default bridge/compose
# address pool, where the frontend container lives.
#
# Trade-off: any peer inside 172.16.0.0/12 can now set its own client IP. In
# the shipped compose file port 8000 is only `expose`d on the internal
# network, so that is just the frontend. If you publish 8000, or your LAN
# itself uses 172.16/12, pin this to the frontend network's exact subnet.
# Note that Next.js 14 passes a client-sent X-Forwarded-For through unchanged
# (it does not append the peer address), so the IP is only trustworthy when a
# reverse proxy in front of the frontend (NPM, Traefik, Caddy) sets the header.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,172.16.0.0/12"

# asyncio loop, not uvloop: uvloop resolves hostnames in C and would bypass
# the getaddrinfo cache in utils/dns_cache.py.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio", "--proxy-headers"]
