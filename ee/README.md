# Nodeglow Enterprise (`ee/`)

This directory holds Nodeglow's commercial features. Its contents are licensed
under the [Nodeglow Enterprise License](LICENSE) (draft, pending legal review),
**not** under the AGPL-3.0 that covers the rest of the repository. See
[`../LICENSING.md`](../LICENSING.md) for the overview.

In short: the code is visible, you may modify it for your own use and run it
for development and testing; production use needs a valid subscription /
license key; redistribution is not permitted.

## What belongs here

- Multi-tenancy and the MSP portal
- SSO / SAML / SCIM
- SLA PDF reports and long-term rollups
- High availability / clustering
- AI premium features
- Audit export / SIEM integration

Everything else — including the building blocks these features use (data
model, collectors, alerting, the plugin hooks themselves) — belongs in the
AGPL core. If a change is useful to every self-hoster, it goes into the core.

## The boundary

The rule is one-way: **`ee/` may depend on the core; the core never depends on
`ee/`.**

- The core must build, start and pass its tests with `ee/` deleted.
- No file outside `ee/` imports from `ee/`, not even behind `try/except
  ImportError` in a code path the core needs. The only place that knows about
  `ee/` is the plugin loader, and it treats "no plugins found" as normal.
- Core code that `ee/` hooks into exposes a documented extension point
  (a registry, a hook, a UI slot) — `ee/` never monkey-patches core internals.
- Database tables owned by `ee/` get their own migrations under `ee/`, so a
  core-only installation never creates them.
- Copying code from `ee/` into the core relicenses it under the AGPL, so it
  needs the owner's decision; copying core code into `ee/` is fine (it stays
  available under the AGPL in the core).

## Intended mechanism

Not implemented yet — this is the design the code should follow.

**Backend (Python).** `ee/backend/` is a separate installable package
(e.g. `nodeglow-ee`) that registers itself through a Python entry point:

```toml
# ee/backend/pyproject.toml
[project.entry-points."nodeglow.plugins"]
ee = "nodeglow_ee:plugin"
```

At startup the core enumerates `importlib.metadata.entry_points(group="nodeglow.plugins")`
and calls each plugin's `register(...)` with a narrow registry object: add
routers, scheduler jobs, notification channels, auth backends, report
renderers, and settings pages. With nothing installed the loop does nothing.

**License key.** The plugin verifies a signed license key (Ed25519, offline
verifiable — the public key ships with `ee/`) on startup and periodically.
Without a valid key the enterprise features stay inactive outside
development/testing mode; the core keeps working unchanged.

**Frontend (Next.js).** The core renders named extension slots (navigation
entries, settings sections, report pages). `ee/frontend/` provides the
components for those slots and is wired in at build time through an optional
module alias that resolves to an empty registry when `ee/` is absent. The
core UI then simply shows no enterprise entries.

**Agent.** The Rust agent stays entirely in the core; it has no enterprise
variant.

## Contributing

Contributions to `ee/` are welcome under the same [CLA](../CLA.md) as the core.
See [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
