# Nodeglow Frontend Specification

> Next.js 14 rewrite of the Nodeglow monitoring UI.
> Backend: FastAPI + PostgreSQL + ClickHouse (unchanged).

---

## 1. Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Next.js (App Router) | 14 |
| Language | TypeScript | 5.x |
| Styling | Tailwind CSS | v4 |
| UI Components | Aceternity UI / Magic UI | latest |
| Charts | Apache ECharts | 5.x |
| 3D | Three.js + @react-three/fiber + drei | latest |
| Animation | Framer Motion | 11.x |
| Data Fetching | TanStack Query (React Query) | 5.x |
| State | Zustand | 4.x |
| Forms | React Hook Form + Zod | latest |
| WebSocket | native + zustand middleware | — |
| Icons | Lucide React + Simple Icons | latest |
| Grid Layout | react-grid-layout | latest |

---

## 2. Design System

### 2.1 Color Palette

```ts
const colors = {
  bg:           { DEFAULT: '#0B0E14', surface: '#111621', elevated: '#1A1F2E' },
  border:       { DEFAULT: '#1E2433', hover: '#2A3144' },
  primary:      { DEFAULT: '#38BDF8', light: '#7DD3FC', dim: '#0C4A6E' },
  accent:       { DEFAULT: '#A78BFA', light: '#C4B5FD', dim: '#4C1D95' },
  success:      { DEFAULT: '#34D399', light: '#6EE7B7', dim: '#064E3B' },
  warning:      { DEFAULT: '#FBBF24', light: '#FDE68A', dim: '#78350F' },
  critical:     { DEFAULT: '#F87171', light: '#FCA5A5', dim: '#7F1D1D' },
  info:         { DEFAULT: '#60A5FA', light: '#93C5FD', dim: '#1E3A5F' },
  text: {
    primary: '#F1F5F9', secondary: '#94A3B8', muted: '#64748B', disabled: '#475569',
  },
  integrations: {
    proxmox: '#E57000', unifi: '#0559C9', unas: '#06B6D4', pihole: '#DC2626',
    adguard: '#10B981', portainer: '#0DB7ED', truenas: '#475569', synology: '#2563EB',
    firewall: '#EA580C', hass: '#F59E0B', gitea: '#16A34A', phpipam: '#9333EA',
    speedtest: '#3B82F6', ups: '#EAB308', redfish: '#7C3AED',
  },
};
```

### 2.2 Typography

| Element | Font | Size | Weight |
|---------|------|------|--------|
| H1 | Inter | 28px | 700 |
| H2 | Inter | 22px | 600 |
| H3 | Inter | 18px | 600 |
| Body | Inter | 14px | 400 |
| Small | Inter | 12px | 400 |
| Mono | JetBrains Mono | 13px | 400 |
| Label | Inter | 11px | 500 |

### 2.3 Visual Effects

| # | Effect | Implementation |
|---|--------|----------------|
| 1 | Glass Card | `bg-white/[0.04] backdrop-blur-xl border border-white/[0.06]` |
| 2 | Glass Elevated | `bg-white/[0.06] backdrop-blur-2xl border border-white/[0.08] shadow-2xl` |
| 3 | Glow Pulse | CSS `@keyframes glow-pulse` with box-shadow |
| 4 | Ambient Orbs | CSS radial-gradient with slow float animation |
| 5 | Gradient Text | `bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent` |
| 6 | Shimmer Loading | Animated gradient sweep left-to-right |
| 7 | Hover Lift | `hover:-translate-y-0.5 hover:shadow-lg transition-all` |
| 8 | Ring Focus | `focus:ring-2 focus:ring-primary/50` |
| 9 | Fade Stagger | Framer Motion `staggerChildren: 0.05` |
| 10 | Slide In | Framer Motion `y: 20 -> 0, opacity: 0 -> 1` |
| 11 | Number Tick | Framer Motion `useMotionValue` + spring |
| 12 | Particle Trail | Three.js shader, fading line segments |
| 13 | Badge Pulse | `animate-pulse` with red glow |
| 14 | Progress Gradient | `bg-gradient-to-r` with color stops |
| 15 | Heatmap Cell | CSS grid + opacity/color transition on hover |

---

## 3. App Router Structure (21 Pages)

```
app/
├── layout.tsx                    # Root: providers
├── login/page.tsx                # Login (/login)
├── setup/page.tsx                # First-run wizard (/setup)
└── (app)/                        # AppShell layout group
    ├── layout.tsx                # Sidebar + Topbar
    ├── page.tsx                  # Dashboard (/)
    ├── hosts/
    │   ├── page.tsx              # Host list
    │   └── [id]/page.tsx         # Host detail
    ├── agents/
    │   ├── page.tsx              # Agent list
    │   └── [id]/page.tsx         # Agent detail
    ├── alerts/page.tsx           # Alerts & incidents
    ├── incidents/[id]/page.tsx   # Incident detail
    ├── rules/page.tsx            # Alert rules
    ├── syslog/
    │   ├── page.tsx              # Syslog viewer
    │   └── templates/page.tsx    # Log templates
    ├── integration/[type]/
    │   ├── page.tsx              # Integration list
    │   └── [id]/page.tsx         # Integration detail
    ├── scanner/page.tsx          # Subnet scanner
    ├── snmp/page.tsx             # SNMP browser
    ├── ssl/page.tsx              # SSL monitor
    ├── credentials/page.tsx      # Credential vault
    ├── digest/page.tsx           # Weekly digest
    ├── settings/page.tsx         # Settings (admin)
    ├── users/page.tsx            # User management (admin)
    └── system/status/page.tsx    # System status
```

---

## 4. Dashboard Widgets (17)

| # | Widget | Default Size | Min | Data Source |
|---|--------|-------------|-----|-------------|
| 1 | Gravity Globe | 12x6 | 6x4 | /hosts/api/status + WS |
| 2 | Heatmap | 12x4 | 6x3 | Dashboard context |
| 3 | Quick Stats | 12x2 | 4x1 | Dashboard context |
| 4 | Host List | 6x4 | 4x3 | host_stats |
| 5 | Offline Hosts | 6x3 | 3x2 | Dashboard context |
| 6 | Integrations | 6x3 | 3x2 | Dashboard context |
| 7 | Active Alerts | 6x3 | 3x2 | /alerts context |
| 8 | SSL Expiry | 6x3 | 3x2 | ssl_certs |
| 9 | Speedtest | 6x3 | 3x2 | latest_speedtest |
| 10 | UPS Status | 6x2 | 3x2 | ups_data |
| 11 | Proxmox | 6x3 | 3x2 | Dashboard context |
| 12 | Storage | 6x3 | 3x2 | storage_data |
| 13 | Containers | 6x3 | 3x2 | Dashboard context |
| 14 | Top 10 | 6x3 | 3x2 | Dashboard context |
| 15 | Uptime Trends | 6x3 | 3x2 | Dashboard context |
| 16 | Syslog Rate | 6x3 | 3x2 | syslog_stats |
| 17 | Clock | 3x2 | 2x1 | Client-side |

---

## 5. Gravity Globe

- **Scene**: PerspectiveCamera fov=50, OrbitControls, UnrealBloomPass
- **Nodes**: Spheres per host (emerald=online, red=offline, amber=maintenance)
- **Connections**: Lines between same-subnet hosts
- **Particles**: 200-500 orbiting particles, primary color at 30% opacity
- **Gravity well**: Particles accelerate toward offline nodes
- **Interaction**: Hover=tooltip, Click=navigate, Right-click=actions
- **HUD**: Online/Offline counters, camera presets (Top/Front/Side/Free)
- **Mobile**: Fallback to 2D status grid

---

## 6. Auth & RBAC

- Session cookie: `nodeglow_session` (httponly, samesite=lax, 30d)
- CSRF: `ng_csrf` cookie + `x-csrf-token` header
- Roles: admin (all), editor (CRUD), readonly (view only)
- `/api/auth/me` endpoint for session check
- Next.js middleware redirects to /login if unauthenticated

---

## 7. Realtime

| Feature | Protocol | Endpoint | Interval |
|---------|----------|----------|----------|
| Host Pings | WebSocket | /ws/live | 60s (server) |
| Agent Metrics | WebSocket | /ws/live | On receipt |
| Syslog Live | SSE | /syslog/stream | Real-time |
| Dashboard | HTTP Polling | /hosts/api/status | 30s |

---

## 8. Implementation Order (22 Steps)

1. Project scaffold
2. Design system (colors, components)
3. Layout shell (Sidebar, Topbar)
4. Auth flow (login, middleware, useAuth)
5. API client (fetch wrapper, CSRF, types)
6. TanStack Query setup
7. Zustand stores (auth, theme, toast, WebSocket)
8. Hosts list page
9. Host detail page
10. Dashboard page + widget grid
11. Dashboard widgets (all 17)
12. WebSocket integration
13. Gravity Globe (Three.js)
14. Syslog page
15. Syslog live tail (SSE)
16. Syslog intelligence
17. Alerts & Incidents
18. Integration pages (16 types)
19. Rules page
20. Agent pages
21. Admin pages (Settings, Users, System, Digest)
22. Polish (animations, loading, errors, mobile)
