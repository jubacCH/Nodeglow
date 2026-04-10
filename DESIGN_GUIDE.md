# Nodeglow Design Guide — Command Center Theme

> Extracted from Stitch designs. Reference for all frontend UI work.

## Philosophy
Nodeglow looks like a **sci-fi command center** — not a typical enterprise dashboard.
Think: Bloomberg Terminal meets Cyberpunk aesthetics meets Apple polish.
Data-dense but beautiful, ambient awareness through color and glow.

---

## Colors

### Background & Surface Scale
| Token | Value | Usage |
|---|---|---|
| `body-bg` | `#060810` | Page background |
| `background` | `#11131c` | Content area |
| `surface-container-lowest` | `#0b0e14` | Deepest cards, terminal bg |
| `surface-container-low` | `#191c22` | Subtle card backgrounds |
| `surface-container` | `#1d2026` | Default card background |
| `surface-container-high` | `#272a31` | Elevated cards, hover states |
| `surface-container-highest` | `#32353c` | Tooltips, dropdowns |
| `surface-bright` | `#363940` | Active/selected surfaces |

### Accent Colors
| Token | Value | Usage |
|---|---|---|
| `primary` | `#8ed5ff` | Primary text, links |
| `primary-container` | `#38bdf8` | Buttons, active elements, main accent |
| `primary-fixed-dim` | `#7bd0ff` | Subtle accent variants |
| `secondary` | `#b0c6ff` | Secondary metrics, tags |
| `secondary-container` | `#06449e` | Secondary backgrounds |
| `tertiary` | `#ffc25a` | Warning, amber states |
| `tertiary-container` | `#eaa400` | Warning backgrounds |

### Status Colors
| Status | Color | Glow Shadow |
|---|---|---|
| Healthy/Online | `#38bdf8` (sky-400) | `0 0 8px rgba(56,189,248,0.5)` |
| Success | `#34d399` (emerald-400) | `0 0 8px rgba(52,211,153,0.5)` |
| Warning | `#ffc25a` (tertiary) | `0 0 8px #ffc25a` |
| Critical/Error | `#ffb4ab` (error) | `0 0 8px #ffb4ab` |
| Offline | `#87929a` (outline) | none |

### Text Colors
| Token | Value | Usage |
|---|---|---|
| `on-surface` | `#e1e1ee` | Primary text, headings |
| `on-surface-variant` | `#bdc8d1` | Secondary text, descriptions |
| `outline` | `#87929a` | Labels, metadata, placeholders |
| `outline-variant` | `#3e484f` | Borders, dividers |

---

## Typography

### Font Families
| Role | Font | Weight Range |
|---|---|---|
| Headlines & Labels | **Space Grotesk** | 300-800 |
| Body & UI | **Inter** | 300-700 |
| Code & Logs | **Geist Mono** | 400 |

### Size Scale
| Context | Size | Weight | Extra |
|---|---|---|---|
| Mega stat numbers | `text-4xl` to `text-6xl` | extrabold (800) | `drop-shadow-[0_0_8px_rgba(56,189,248,0.5)]` |
| Stat card numbers | `text-2xl` | bold (700) | |
| Page title | `text-3xl` | bold | |
| Section heading | `text-xl` | bold | `tracking-tight` |
| Sub heading | `text-lg` | semibold (600) | |
| Body text | `text-sm` | normal (400) | |
| Labels | `text-[10px]` | bold (700) | `uppercase tracking-widest` |
| Micro labels | `text-[9px]` | medium (500) | `uppercase tracking-widest` |
| Log/code text | `text-[11px]` to `text-[13px]` | normal | `font-mono leading-relaxed` |

---

## Effects

### Glass Panel (Primary Card Style)
```css
background: rgba(255, 255, 255, 0.03);
border: 1px solid rgba(56, 189, 248, 0.1);
backdrop-filter: blur(12px);
```

### Glass Card (Alternative)
```css
background: rgba(50, 53, 60, 0.6);
backdrop-filter: blur(16px);
border: 1px solid rgba(142, 213, 255, 0.05);
```

### Body Background Grid
```css
background-color: #060810;
background-image:
  linear-gradient(rgba(56, 189, 248, 0.03) 1px, transparent 1px),
  linear-gradient(90deg, rgba(56, 189, 248, 0.03) 1px, transparent 1px);
background-size: 24px 24px;
```

### Ambient Glow (Fixed Overlay)
```css
background: radial-gradient(circle at 50% -20%, #38bdf8 0%, transparent 50%);
opacity: 0.2;
position: fixed;
pointer-events: none;
```

### Key Shadows
| Context | Shadow |
|---|---|
| Sidebar glow | `0 0 15px rgba(56,189,248,0.1)` |
| Top nav | `0 4px 20px rgba(0,0,0,0.5)` |
| Active nav item | `inset 0 0 10px rgba(56,189,248,0.3)` |
| Accent glow | `0 0 5px #38bdf8` |
| Error glow | `0 0 8px #ffb4ab` |
| Warning glow | `0 0 8px #ffc25a` |
| FAB button | `0 0 20px rgba(58,223,250,0.4)` |

---

## Layout

### Sidebar
- **Width**: `60px` (icon-only mode) / `256px` (expanded with labels)
- **Background**: `surface-container-lowest` (`#0b0e14`)
- **Position**: Fixed left, full height
- **Nav items**: Icon + optional label, active item has cyan glow
- **Active indicator**: Left border `border-l-2 border-primary` or inset glow

### Top Nav
- **Height**: `64px` (`h-16`)
- **Position**: Fixed top, offset by sidebar width
- **Background**: `surface-container-low/80` with `backdrop-blur-xl`
- **Shadow**: `0 4px 20px rgba(0,0,0,0.5)`
- **Brand**: `text-sky-400 font-black tracking-tighter`

### Content Area
- **Margin**: `ml-[60px]` (icon sidebar) or `ml-64` (full sidebar)
- **Padding**: `pt-16 p-6` to `pt-20 px-8 pb-12`
- **Grid**: `grid-cols-12` as base, responsive with `gap-6`

---

## Component Patterns

### Stat Card
```
glass-panel p-4 h-32 relative overflow-hidden
  Label: text-[10px] uppercase tracking-widest text-outline (top)
  Icon: material-symbols text-sky-400 (top-right)
  Value: text-4xl font-extrabold text-sky-400 drop-shadow-glow
  Progress: h-1 bar at bottom with colored fill + glow
```

### Data Table
```
glass-panel overflow-hidden
  Header: bg-surface-container-high/50, text-[10px] uppercase tracking-widest
  Rows: border-b border-outline-variant/10, hover:bg-surface-container-high/30
  Status dot: w-2 h-2 rounded-full with glow shadow
  Values: font-mono text-sm
```

### Severity Badges
```
text-[10px] font-bold uppercase px-2 py-0.5 rounded
  Critical: bg-error/20 text-error
  Warning: bg-tertiary/20 text-tertiary
  Info: bg-primary/20 text-primary
  Debug: bg-outline/20 text-outline
```

### Incident Cards
```
bg-surface-container-low border-l-2 border-{severity} p-3
  Title: font-headline font-bold text-sm
  Time: text-[10px] text-outline
  Severity badge: inline
```

### Chart Containers
```
glass-panel p-6
  Title: text-sm font-headline font-bold uppercase tracking-wider
  Chart area: h-[200px] to h-[300px]
  Mini sparklines: h-8, bars with opacity ramp
  SVG charts: gradient fill, 2px stroke, glow on data points
```

### Status Indicators
| Type | Implementation |
|---|---|
| Online dot | `w-2 h-2 rounded-full bg-primary animate-pulse` |
| Offline dot | `w-2 h-2 rounded-full bg-outline` |
| Warning dot | `w-2 h-2 rounded-full bg-tertiary shadow-[0_0_8px_#ffc25a]` |
| Error dot | `w-2 h-2 rounded-full bg-error shadow-[0_0_8px_#ffb4ab] animate-pulse` |
| Left border | `border-l-2 border-{color}` on cards |

---

## Animations
| Animation | Usage |
|---|---|
| `animate-pulse` | Live indicators, active incidents, critical stats |
| `transition-all duration-200` | Buttons, nav items |
| `transition-all duration-300` | Panels, expanded states |
| `hover:scale-110` | FAB buttons |
| `active:scale-95` | Action buttons |
| `hover:brightness-110` | Primary buttons |

---

## Scrollbar
```css
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); }
::-webkit-scrollbar-thumb { background: #454751; border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: #38bdf8; }
```

---

## Icons
- **System**: Google Material Symbols Outlined (variable font)
- **Settings**: `FILL: 0, wght: 300, GRAD: 0, opsz: 24`
- **Lucide React** for inline icons (existing)

---

## Key Design Rules

### Glow discipline (revised April 2026)
**Glow is a signal, not decoration.** When everything glows, nothing does.
Reserve glow effects for the two cases where movement and light actually
help the user:

1. **State change in the last 60 seconds** — a host that just went offline,
   an incident that just opened, a metric that just spiked. Glow draws the
   eye to *what changed*, not to what's been steady-state for hours.
2. **Active critical incident** — currently-firing problems. Healthy /
   online / steady-state must be quiet.

Concretely:
- Online StatusDot → solid color, no shadow, no pulse
- Offline StatusDot → red, soft shadow, optional `pulse` flag (already
  enforced by the component)
- Stat-card numbers → bold and large, but **no drop-shadow**. The size
  alone carries the visual weight.
- Buttons / links / nav → no glow. Hover state is enough.
- Live data feeds → may use a tiny pulse on the "streaming" indicator,
  not on the rows themselves.

The previous "every accent color glows" rule is retired. It produced a
dashboard where the eye had nowhere to land.

### Other rules
1. **Labels are always** `text-[10px] uppercase tracking-widest`
2. **Numbers are large and bold** — stats use `text-3xl` to `text-4xl`,
   weight 700-800. Size is the signal, not glow.
3. **Glass everywhere** — every card uses `backdrop-blur` + translucent bg
4. **Grid pattern on body** — subtle 24px grid lines in cyan
5. **Ambient glow overlay** — radial gradient from top center (page level
   only, not on individual elements)
6. **Sharp corners** on Command Center, small radius on detail pages
7. **Color = meaning** — cyan=healthy, amber=warning, red=critical, gray=inactive
8. **Every widget answers a question** — if a panel doesn't answer a
   question in <2 seconds it's decoration. Cut it.
