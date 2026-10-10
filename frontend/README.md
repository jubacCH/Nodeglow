# Nodeglow frontend

The web UI: Next.js 15 (App Router, `output: 'standalone'`), React 19,
TypeScript, Tailwind CSS 3, TanStack Query, Zustand, ECharts, lucide-react.
It has no data of its own — every request goes to the backend through the
rewrites in `next.config.mjs`.

## Development

Requires Node 22 (the version the Docker image and CI use) and a running
backend.

```bash
cd frontend
npm ci
BACKEND_URL=http://<backend-host>:8000 npm run dev    # http://localhost:3000
```

`BACKEND_URL` is where the rewrites send `/api/*`, `/ws/*`, `/settings/*` and
the other backend routes; it defaults to `http://nodeglow:8000`, the backend
service inside the compose network. The backend container only `expose`s port
8000, so for local development either publish it in a
`docker-compose.override.yml` or run the backend locally.

| Command | |
|---|---|
| `npm run dev` | Generate the design tokens, start the dev server |
| `npm run build` | Generate the tokens, production build (what the Docker image runs) |
| `npm run build:tokens` | Only regenerate `src/styles/tokens.gen.{css,ts}` from `design-tokens/tokens.json` |
| `npm run lint` | ESLint (Next config + jsx-a11y) |
| `npx tsc --noEmit` | Type check |
| `npm run test` | Vitest unit tests |
| `npm run check:contrast` | WCAG contrast check of the token palette |

CI runs type check, lint, tests and build — see
[CONTRIBUTING.md](../CONTRIBUTING.md#tests-and-checks).

## Where things are

| Path | |
|---|---|
| `src/app/(app)/` | Pages behind login; `src/app/login/` is the login page |
| `src/lib/navigation.ts` | **Source of truth** for sections, routes, labels, badges and `g …` shortcuts (rail, sub-navigation, mobile tab bar, command palette). Every route must resolve to an item here — a test enforces it |
| `src/components/layout/` | App shell: icon rail, top bar, section sub-navigation, mobile tab bar, license banner |
| `src/components/ui/` | Design-system components — use these, not ad-hoc markup |
| `design-tokens/tokens.json` | Colour, type and spacing tokens for light and dark; generated into `src/styles/tokens.gen.*` and used through semantic Tailwind classes (`bg-surface`, `text-fg-2`, `text-down`, …) |
| `src/lib/theme.ts` | Light / dark / system theme |
| `src/app/fonts.ts` | Self-hosted fonts (Sora, Inter Tight, JetBrains Mono from `@fontsource-variable/*`) — the build needs no network access |
| `src/lib/features.ts` | `useFeatures()` / `hasFeature()` — show enterprise features only when `/api/v2/features` reports them |
| `src/hooks/queries/` | TanStack Query hooks per API area |
| `src/lib/` | API client (`api.ts`), mapping helpers and their unit tests |

The design rules (tokens, components, glow and state colours, shell) are in
[docs/design/04-design-system.md](../docs/design/04-design-system.md)
(German). Short version: semantic classes only, components from
`components/ui`, no hex values, no `slate-*`.

## License

AGPL-3.0-only, like the rest of the core — including the screens for
enterprise features (see [LICENSING.md](../LICENSING.md)).
