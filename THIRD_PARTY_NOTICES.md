# Third-party notices

Nodeglow uses the third-party components listed below. Each is licensed under
its own terms; nothing in Nodeglow's licenses changes them. This list covers
**direct runtime dependencies**; transitive dependencies come with their own
license files in the respective package distributions.

Licenses were taken from the package registries for the pinned/locked
versions (PyPI JSON API, `frontend/package-lock.json`, crates.io API) on
2026-10-10. When you change a dependency, update this file.

## Compatibility summary

All direct dependencies are under permissive licenses (MIT, BSD, Apache-2.0,
ISC) or the LGPL, which are compatible with distributing Nodeglow under the
AGPL-3.0-only and with the commercial `ee/` license. Points to watch:

| Component | License | Status |
|---|---|---|
| **Ookla Speedtest CLI** | Proprietary (Ookla EULA) | **Not bundled by default.** The backend image installs it only when built with `BUNDLE_OOKLA=1`; such an image must not be redistributed. Without it, the integration falls back to the Apache-2.0 `speedtest-cli` package. Operators who opt in accept Ookla's terms themselves. |
| **MaxMind GeoLite2 database** | GeoLite2 EULA (MaxMind) | **Not redistributed.** The backend downloads it at runtime with the operator's own MaxMind license key; the operator is bound by MaxMind's terms. Nodeglow ships only the Apache-2.0 `maxminddb` reader. |
| `ldap3` | LGPL-3.0 | Compatible. Used unmodified as a separate library; if it is modified, the modified version must be provided under the LGPL. |
| `sharp` / `libvips` (transitive, Next.js image optimisation) | Apache-2.0 / LGPL-3.0-or-later | Compatible. Shipped as separate, dynamically loaded prebuilt binaries. |
| `dompurify` (transitive, via `isomorphic-dompurify`) | MPL-2.0 OR Apache-2.0 | Compatible; used under Apache-2.0. |
| `caniuse-lite` (transitive, build-time data) | CC-BY-4.0 | Compatible; attribution via its package. |
| `webgl-constants` (transitive, 3D view) | no license field in the package metadata | **To verify** with the upstream repository. |
| `webpki-roots` (transitive, Rust agent) | CDLA-Permissive-2.0 | Compatible (permissive data license; keep its notice). |
| `ring`, `untrusted` (transitive, Rust agent) | Apache-2.0 AND ISC / ISC | Compatible. |
| Fonts Inter, JetBrains Mono (via `next/font/google`, self-hosted at build) | SIL Open Font License 1.1 | Compatible. |
| `anthropic` SDK | MIT | Compatible. Using the Anthropic API is subject to Anthropic's own terms, accepted by whoever supplies the API key. |

The prebuilt agent binaries in `backend/static/` statically link all
transitive Rust crates (239 packages in `agent/Cargo.lock`, all permissive as
far as checked). Their MIT/BSD/Apache/ISC attribution requirements apply to
those binaries. Recommendation: generate a full attribution file in the
agent build (e.g. with `cargo about`) and run `cargo deny check licenses` in
CI.

## Backend (`backend/requirements.txt`)

| Package | Version | License |
|---|---|---|
| fastapi | 0.136.3 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| uvicorn[standard] | 0.54.0 | BSD-3-Clause |
| sqlalchemy[asyncio] | 2.1.3 | MIT |
| jinja2 | 3.1.6 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| apscheduler | 3.11.3 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| asyncpg | 0.32.0 | Apache-2.0 |
| bcrypt | 5.0.0 | Apache-2.0 |
| aiosmtplib | 5.1.3 | MIT |
| speedtest-cli | 2.1.3 | Apache-2.0 |
| alembic | 1.20.0 | MIT |
| psutil | 7.2.2 | BSD-3-Clause |
| clickhouse-connect | 0.8.9 | Apache-2.0 |
| maxminddb | 3.2.0 | Apache-2.0 |
| anthropic | 1.13.0 | MIT |
| ldap3 | 2.9.1 | LGPL-3.0 |
| redis | 8.1.0 | MIT (Python client only; no Redis server is shipped) |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| opentelemetry-api | 1.29.0 | Apache-2.0 |
| opentelemetry-sdk | 1.29.0 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-http | 1.29.0 | Apache-2.0 |
| opentelemetry-instrumentation-fastapi | 0.50b0 | Apache-2.0 |
| opentelemetry-instrumentation-sqlalchemy | 0.50b0 | Apache-2.0 |
| opentelemetry-instrumentation-httpx | 0.50b0 | Apache-2.0 |

The sidecar and the Python agents (`backend/static/*.py`) use the Python
standard library only.

## Frontend (`frontend/package.json` dependencies)

Versions as resolved in `frontend/package-lock.json`.

| Package | Version | License |
|---|---|---|
| @react-three/drei | 10.7.9 | MIT |
| @react-three/fiber | 9.8.1 | MIT |
| @tanstack/react-query | 5.104.1 | MIT |
| clsx | 2.1.1 | MIT |
| cmdk | 1.1.1 | MIT |
| echarts | 6.1.0 | Apache-2.0 |
| framer-motion | 12.43.0 | MIT |
| isomorphic-dompurify | 4.5.0 | MIT |
| lucide-react | 0.577.0 | ISC |
| next | 15.5.27 | MIT |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| tailwind-merge | 3.7.0 | MIT |
| three | 0.186.1 | MIT |
| zustand | 5.0.15 | MIT |

Across all non-dev packages in the lockfile: MIT 131, Apache-2.0 22,
LGPL-3.0-or-later 10 and mixed Apache/LGPL 4 (all `sharp`/`libvips`
platform binaries), ISC 7, BSD-3-Clause 4, 0BSD 3, MIT-0 2, BSD-2-Clause 2,
and one each of CC-BY-4.0, MPL-2.0 OR Apache-2.0, BlueOak-1.0.0, CC0-1.0 and
unspecified (see the summary above).

## Agent (`agent/Cargo.toml` dependencies)

Versions as resolved in `agent/Cargo.lock`.

| Crate | Version | License |
|---|---|---|
| tokio | 1.53.2 | MIT |
| reqwest | 0.12.28 | MIT OR Apache-2.0 |
| serde | 1.0.229 | MIT OR Apache-2.0 |
| serde_json | 1.0.151 | MIT OR Apache-2.0 |
| sha2 | 0.11.0 | MIT OR Apache-2.0 |
| tracing | 0.1.44 | MIT |
| tracing-subscriber | 0.3.23 | MIT |
| hostname | 0.4.2 | MIT |
| clap | 4.6.7 | MIT OR Apache-2.0 |
| hex | 0.4.3 | MIT OR Apache-2.0 |
| chrono | 0.4.45 | MIT OR Apache-2.0 |
| anyhow | 1.0.104 | MIT OR Apache-2.0 |
| ed25519-dalek | 3.0.0 | BSD-3-Clause |
| sysinfo (Windows only) | 0.32.1 | MIT |
| winresource (Windows build only) | 0.1.31 | MIT |

## Container images used by `docker-compose.yml`

Pulled by the operator from their publishers, not redistributed by Nodeglow:
`postgres:16-alpine` (PostgreSQL License), `clickhouse/clickhouse-server`
(Apache-2.0). The Nodeglow images build on `python:3.12-slim` and
`node:22-alpine`, which contain Debian/Alpine packages under their own
licenses (including GPL components); anyone redistributing built images must
comply with those package licenses as well.

## AGPL §13 and the hosted service

Section 13 of the AGPL-3.0 requires that users who interact with a
**modified** version of the core over a network are offered the Corresponding
Source of that version. For the hosted (SaaS) offering and for anyone running
a modified Nodeglow for others, this means:

- the exact core source of the running version (including local
  modifications) must be available, e.g. a tag or commit in a public
  repository;
- users should be pointed to it from the application itself.

**Recommendation (not implemented yet):** add a "Source code" link to the UI
footer and the About page that points to the repository at the running
version (`VERSION` / commit hash). Third parties hosting a modified core are
bound by §13 directly; for the project's own hosted service the link keeps
the open-core promise visible. The `ee/` code is not licensed under the AGPL
and is not part of that Corresponding Source.
