# Licensing

Nodeglow is developed as **Open Core**: a free and open source core plus a
separately licensed set of commercial features.

| Part of the repository | License | File |
|---|---|---|
| Everything **outside** the `ee/` directory (backend, frontend, Rust agent, sidecar, Python agents, ClickHouse config, docs) | GNU Affero General Public License v3.0 only (`AGPL-3.0-only`) | [`LICENSE`](LICENSE) |
| Everything **inside** the `ee/` directory | Nodeglow Enterprise License (proprietary, source-available) — **draft** | [`ee/LICENSE`](ee/LICENSE) |
| Third-party components | Their respective licenses | [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |

If a file does not say otherwise, its license is determined by its location:
inside `ee/` → Nodeglow Enterprise License, anywhere else → AGPL-3.0-only.

## The core (AGPL-3.0-only)

You may use, study, modify and redistribute the core under the terms of the
AGPL-3.0. In particular, AGPL §13 applies: if you run a **modified** version of
Nodeglow and let users interact with it over a network, you must offer those
users the Corresponding Source of your modified version.

The core is complete on its own — it builds and runs without the `ee/`
directory. Removing `ee/` removes the commercial features and nothing else.

## Commercial features (`ee/`)

Features intended for businesses and service providers live in `ee/`.

**In `ee/` today:**

- **High availability** — scheduler leader election across several backend
  processes/replicas (Redis lease; only the holder runs scheduled jobs).
  The core runs single-instance. The Redis-backed **rate limiter stays in
  the core**: it is a security control every installation needs.
- **AI features** — Glow (the AI assistant chat), AI postmortem drafts for
  resolved incidents (automatic and on demand), and the AI daily summary
  (scheduled and test send). The AI *plumbing* stays in the core: the
  provider abstraction (`services/ai_client.py`), the opt-in and provider
  settings (`services/ai_config.py`, Settings → AI), redaction
  (`services/ai_redaction.py`) and token-usage accounting.

**Planned for `ee/`:**

- multi-tenancy and the MSP portal
- SSO via SAML / OIDC, and SCIM provisioning
- custom roles (RBAC beyond admin / editor / read-only)
- on-call schedules and escalation
- SLA reports per customer (PDF) and long-term rollups
- audit log export / SIEM integration and long audit retention

**What stays AGPL even though it serves `ee/`:** the frontend. All UI code,
including the screens for enterprise features, lives in `frontend/` under the
AGPL; it asks `GET /api/v2/features` which features are installed and shows
only those. The commercial part is the backend logic in `ee/`. See
[`ee/README.md`](ee/README.md) for the structure and the reasons.

The source is visible, and you may use it for development and testing.
Running it in production requires a valid Nodeglow Enterprise subscription or
license key. License keys are not enforced yet (see `ee/README.md`); the
license terms apply regardless. See [`ee/LICENSE`](ee/LICENSE).

**Running without `ee/`:** delete the directory, build without it, or set
`NODEGLOW_DISABLE_EE=1`. The result is the community edition: every core
feature works, the enterprise endpoints do not exist, and the UI hides them
(with a one-line note where a user would look for them).

## Published images

Releases publish two backend images (see [docs/INSTALL.md](docs/INSTALL.md#editions-and-images)):
`nodeglow-backend` contains `ee/` and is therefore partly under the Nodeglow
Enterprise License; `nodeglow-backend-community` is built without `ee/` and
contains AGPL-3.0 code only. The frontend and updater images are AGPL-3.0
in both cases. Each image states its license in the
`org.opencontainers.image.licenses` label.

## Obtaining a commercial license

A commercial license covers production use of `ee/`, and can also be
offered for use of the core without the AGPL obligations (dual licensing).

Contact: the repository owner, [@jubacCH on GitHub](https://github.com/jubacCH).

<!-- TODO(owner): add a dedicated licensing email address, e.g. licensing@<domain>. -->
**TODO:** a dedicated licensing email address will be published here.

## Contributions

External contributions are accepted under the
[Contributor License Agreement](CLA.md), which lets the project offer the code
under both the AGPL and commercial terms. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Earlier versions

This licensing structure applies from the commit that introduced this file
onwards.

- Versions of Nodeglow published **before** this change did not ship a license
  file. Except as noted below, those versions were not licensed to the public
  and remain "all rights reserved".
- The Rust agent crate (`agent/`) declared `license = "MIT"` in its
  `Cargo.toml` before this change. Earlier agent versions published under MIT
  remain available under MIT. From this change onwards, the agent is licensed
  under AGPL-3.0-only.

## Trademarks

The licenses above cover copyright only. They do not grant any right to use
the name "Nodeglow" or its logos beyond what is needed to describe the origin
of the software.
