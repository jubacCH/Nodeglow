# Contributing to Nodeglow

Thanks for helping. Bug reports, fixes, integrations and docs are all welcome.

## Before you start

- **Bugs and small fixes:** open a pull request directly, or an issue first if
  you're unsure it's a bug.
- **Larger changes** (new integrations, schema changes, new pages): open an
  issue describing the idea first, so we can agree on the approach before you
  invest the time.
- **Security issues:** do not open a public issue. Use GitHub's private
  vulnerability reporting ("Security" tab → "Report a vulnerability").

## Licensing of contributions

Nodeglow is Open Core (see [`LICENSING.md`](LICENSING.md)): the core is
AGPL-3.0-only, the `ee/` directory is under the commercial Nodeglow
Enterprise License.

To make that possible, every external contribution needs two things:

1. **The CLA, once.** Read the [Contributor License Agreement](CLA.md)
   (currently a draft pending legal review) and accept it by posting the
   comment shown at the end of `CLA.md` on your first pull request. A pull
   request cannot be merged until its author — and every co-author — has
   signed. If you contribute on behalf of your employer, make sure you are
   allowed to (CLA §7) — ask for the corporate agreement if needed.
2. **A sign-off on every commit** (Developer Certificate of Origin,
   <https://developercertificate.org/>), which records that you have the
   right to submit that commit:

   ```bash
   git commit -s -m "fix: …"
   # adds: Signed-off-by: Your Name <you@example.com>
   ```

   Use the same email address you listed when signing the CLA.

Don't submit code you copied from elsewhere unless its license is compatible
with the AGPL-3.0 *and* with commercial redistribution (MIT, BSD, Apache-2.0,
ISC and similar), and say where it came from in the pull request. New
dependencies need the same check — add them to
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Changes in `ee/` must not be imported from core code — see
[`ee/README.md`](ee/README.md) for the boundary.

## Development setup

See the [README](README.md) for running the stack with Docker Compose.

| Part | Location | Stack |
|---|---|---|
| Backend | `backend/` | Python 3.12, FastAPI, SQLAlchemy, Alembic |
| Frontend | `frontend/` | Next.js, React, TypeScript, Tailwind |
| Agent | `agent/` | Rust (stable, MSRV in `Cargo.toml`) |
| Sidecar | `sidecar/` | Python update orchestrator |

## Tests and checks

Run the checks for the part you changed before opening a pull request; CI
runs the same ones.

```bash
# Backend
cd backend
pip install -r requirements-dev.txt
pytest -q
ruff check .                                   # CI pins ruff==0.17.0

# Sidecar (from the repo root)
ruff check --config backend/ruff.toml sidecar
cd sidecar && python -m pytest tests -q

# Frontend
cd frontend
npm ci
npx tsc --noEmit
npm run lint
npm run test
npm run build

# Agent
cd agent
cargo build --release
cargo test --release
cargo fmt --check && cargo clippy               # recommended, not yet in CI
```

Add or update tests with your change — a bug fix should come with a test
that fails without it. Database changes need an Alembic migration.

## Commit style

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <summary in imperative mood, lowercase>

Why the change is needed and what it does, wrapped at ~72 columns.
Mention measurable effects where relevant (e.g. "~670 ms -> ~140 ms").

Signed-off-by: Your Name <you@example.com>
```

Common types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`,
`chore` (e.g. `chore(deps): …`). Keep one logical change per commit, and keep
pull requests focused — unrelated cleanups go in a separate pull request.

## Pull requests

- Describe what changed and why, and how you tested it.
- Include screenshots for UI changes.
- Keep the PR up to date with `main`; the maintainer may squash on merge.
