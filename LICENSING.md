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

Features intended for businesses and service providers live in `ee/`, for
example:

- multi-tenancy and the MSP portal
- SSO / SAML / SCIM
- SLA PDF reports and long-term rollups
- high availability / clustering
- AI premium features
- audit export / SIEM integration

The source is visible, and you may use it for development and testing.
Running it in production requires a valid Nodeglow Enterprise subscription or
license key. See [`ee/LICENSE`](ee/LICENSE) and [`ee/README.md`](ee/README.md).

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
