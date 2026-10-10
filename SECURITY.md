# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub:
[Security → Report a vulnerability](https://github.com/jubacCH/Nodeglow/security/advisories/new).
Include what you found, how to reproduce it, the affected version (Settings →
System or the `VERSION` file) and, if you have one, a suggested fix.

You can expect an acknowledgement within a few days. Fixes are released as a
new version and noted in the [CHANGELOG](CHANGELOG.md); reporters are credited
unless they prefer not to be.

## Supported versions

Security fixes go into the latest release. There are no long-term support
branches yet.

## Scope

In scope: the backend, the frontend, the update sidecar, the Rust agent, the
install scripts and the published container images.

Nodeglow is built to run inside a trusted network or behind a reverse proxy
with TLS. Findings that require an already authenticated administrator, or
access to the Docker host, are usually not vulnerabilities — report them anyway
if you are unsure.

## Verifying releases

Release images and the release checksums are signed with cosign (keyless,
GitHub OIDC). See [docs/INSTALL.md](docs/INSTALL.md#verifying-signatures) for how to verify them.
