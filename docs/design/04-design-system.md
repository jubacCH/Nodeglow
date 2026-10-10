# 04 — Design-System (Phase D)

| | |
|---|---|
| **Stand** | 10.10.2026 |
| **Grundlage** | Konzept E3 „Nodeglow“ ([`directions/concept-e3.md`](directions/concept-e3.md), Prototyp [`prototypes/concept-e3/index.html`](prototypes/concept-e3/index.html)), IA aus [`02-information-architecture.md`](02-information-architecture.md) |
| **Akzent** | nur **Glow Violet** (`#7C6CFF` dunkel / `#5B4BE0` hell), als Token umgesetzt; der Akzent-Umschalter des Prototyps entfällt |
| **Code** | `frontend/design-tokens/tokens.json` → `src/styles/tokens.gen.{css,ts}`, `tailwind.config.ts`, `src/components/ui/*`, `src/components/layout/*`, `src/lib/{theme,status,navigation,chart-theme}.ts` |

Dieses Dokument ist die Arbeitsgrundlage für alle Agents, die in Phase F Seiten migrieren oder das neue Dashboard bauen. Kurzfassung: **Nur semantische Klassen (`bg-surface`, `text-fg-2`, `text-down` …), nur Komponenten aus `components/ui`, keine Hex-Werte, kein `slate-*`.**

---

## 1. Token-Pipeline

```
design-tokens/tokens.json ──(npm run build:tokens, läuft auch in dev/build)──▶ src/styles/tokens.gen.css   CSS-Variablen --ng-*
                                                                         └──▶ src/styles/tokens.gen.ts    Namen + Rohwerte für JS
```

- Jeder Schlüssel wird zur CSS-Variable `--ng-<name>`. `{name}` im Wert ist eine Referenz und wird zu `var(--ng-name)`; so folgen abgeleitete Tokens (Soft-Töne, Glow) automatisch dem Theme.
- `theme.dark` gilt auf `:root` (Default), `theme.light` auf `[data-theme="light"]`. `shared` (abgeleitete und themeunabhängige Tokens) wird auf `:root, [data-theme]` geschrieben, damit ein Teilbaum mit eigenem `data-theme` (z. B. Toast, Tooltip: immer dunkel) korrekt rechnet.
- Das Build-Skript prüft: beide Themes haben dieselben Schlüssel, jede Referenz existiert.
- `contrast.pairs` in der JSON listet die Text/Hintergrund-Paare, die `src/styles/tokens.test.ts` (Teil von `npx vitest run`) in beiden Themes nach WCAG prüft. Tabelle ausgeben: `npm run check:contrast`.

### Theme-Mechanik

- Aktives Theme = `data-theme="dark|light"` auf `<html>`. Kein `className="dark"`, keine Slate-Klassen am `<body>`.
- **Kein Aufblitzen:** `THEME_INIT_SCRIPT` (`src/lib/theme.ts`) läuft inline im `<head>` von `app/layout.tsx`, liest den persistierten Theme-Store (`localStorage["ng-theme"]`) und setzt `data-theme`, `color-scheme` und die Schriftgrösse vor dem ersten Paint.
- `<ThemeController>` (in `Providers`) hält danach alles synchron: Store-Änderungen, `prefers-color-scheme`, andere Tabs.
- Store `useThemeStore` (`stores/theme.ts`): `colorMode: 'dark' | 'light' | 'system'` (Default **dark**; „system“ folgt dem Betriebssystem), `toggleColorMode()`, `fontSize` (Root-Schriftgrösse, Default 14 px), `density`. (Die wirkungslosen `accentColor`/`sidebarPosition` sind entfernt.)
- Für JS/Canvas: `useResolvedTheme()` (`lib/useResolvedTheme.ts`) liefert das tatsächlich angewandte Theme und rendert bei jedem Wechsel neu.

---

## 2. Tokens

### 2.1 Farben pro Theme

| Token (`--ng-…`) | Dunkel | Hell | Tailwind | Verwendung |
|---|---|---|---|---|
| `bg` | `#101217` | `#F4F5F8` | `bg-bg` | Seitenhintergrund, Kopfzeile |
| `rail` | `#0C0E12` | `#FFFFFF` | `bg-rail` | Navigationsleiste, mobile Tab-Bar |
| `surface` | `#181A20` | `#FFFFFF` | `bg-surface` | Karten, Panels, Dialoge |
| `surface-2` | `#1F222A` | `#F2F3F6` | `bg-surface-2` | Hover, Kacheln in Karten, sekundäre Buttons |
| `surface-3` | `#282B34` | `#E8EAEF` | `bg-surface-3` | aktive Segmente, Avatare, Balken-Hintergrund |
| `border` | `rgba(255,255,255,.06)` | `#E3E5EA` | `border-border` | Kartenrand, Trennlinien |
| `border-2` | `rgba(255,255,255,.11)` | `#D6D9E0` | `border-border-2` | Eingabefelder, Popover, Hover-Rand |
| `line` | `#363A45` | `#C9CED7` | `border-line` / `bg-line` | Leitungen, Achsen, Checkbox-Rand |
| `hover` | `rgba(255,255,255,.04)` | `rgba(20,24,32,.035)` | `bg-hover` | dezente Zeilen-Hover |
| `text` | `#ECEEF2` | `#191B21` | `text-fg` | Fliesstext, Titel, Werte |
| `text-2` | `#A2A6B1` | `#5A5E69` | `text-fg-2` | Sekundärtext, Labels |
| `text-3` | `#8A8F9B` | `#666B76` | `text-fg-3` | Metadaten, Platzhalter, inaktive Icons |
| `accent` | `#7C6CFF` | `#5B4BE0` | `bg-accent`, `border-accent` | Linien, Charts, aktive Markierung |
| `accent-text` | `#9488FF` | `#5242D6` | `text-accent` | Links, aktiver Navigationspunkt |
| `accent-hover` | `#8F81FF` | `#4C3CD2` | `text-accent-hover` | Link-Hover |
| `accent-btn` / `-hover` | `#6656F2` / `#5A4AE6` | `#5B4BE0` / `#4C3CD2` | `bg-accent-btn` | Primär-Button |
| `on-accent` | `#FFFFFF` | `#FFFFFF` | `text-on-accent` | Text auf Primär-Button |
| `focus` | `#9488FF` | `#5B4BE0` | `outline-focus` | Fokusring (global gesetzt) |
| `st-ok` | `#3CC37A` | `#1E9E57` | `bg-ok` | OK (nur frische, bestätigte Messung) |
| `st-degraded` | `#E7C04C` | `#B98A00` | `bg-degraded` | Beeinträchtigt |
| `st-warning` | `#F2923C` | `#DD6B12` | `bg-warning` | Warnung |
| `st-down` | `#F2545B` | `#DC3540` | `bg-down` | Ausgefallen / kritisch |
| `st-maint` | `#8F97A8` | `#6F7788` | `bg-maint` | Wartung (grau-blau, nie Amber) |
| `st-unknown` | `#565B66` | `#A4A8B0` | `bg-unknown` (nur für Ränder/Schraffur) | Keine Daten |
| `st-*-text` | wie Statusfarbe (`down` `#F46A70`, `maint` `#A3AABA`) | dunklere AA-Varianten (`#167A43`, `#806000`, `#AE520B`, `#C42A35`, `#5A6170`) | `text-ok`, `text-down` … | Statustext (automatisch über `text-*`) |
| `st-*-soft` | `color-mix(Status 13 %)`, Wartung 15 %, Unbekannt 20 % | gleich | `bg-ok-soft` … | Pills, Chips |
| `accent-soft` | Akzent 16 % | Akzent 10 % | `bg-accent-soft` | aktive Navigation, Auswahl |
| `chart-fill` | Akzent 18 % | Akzent 12 % | — | Flächen unter Linien |
| `chart-1…6` | Violett, Blau `#5AA9F0`, Magenta `#E37BD0`, Grau, Hellviolett, Tiefblau | dunklere Pendants | `useChartTheme().series` | kategoriale Reihen ohne Statusbedeutung |
| `hatch-2`, `hatch` | `#252830` + Streifen | `#E3E5E9` + Streifen | `.ng-hatch` | Schraffur „keine Daten“ |
| `grid` | `rgba(255,255,255,.05)` | `#EEF0F3` | `bg-grid` | Chart-Raster |
| `scrim` | `rgba(4,5,8,.6)` | `rgba(20,22,28,.28)` | `bg-scrim` | Overlay hinter Dialog/Panel |
| `tip-bg` / `tip-text` | `#262932` / `#ECEEF2` | `#1D1F25` / `#F2F3F5` | `bg-tip`, `text-tip-fg` | Tooltip, Toast (immer dunkel) |
| `shadow`, `shadow-sm` | tief | weich | `shadow-overlay`, `shadow-ng-sm` | nur schwebende Flächen (Popover, Panel, Dialog) |

Abweichungen vom Prototyp (wegen WCAG AA, siehe 2.5): `text-3` dunkel `#6E7380 → #8A8F9B`, hell `#868B96 → #666B76`; `accent-btn-hover` dunkel `#7363FA → #5A4AE6` (Hover wird dunkler statt heller, der Akzent-Glow liefert das Feedback); neue `st-*-text`-Tokens.

### 2.2 Glow-Tokens

| Token | Dunkel (Licht) | Hell (getönter Schatten) | Tailwind / Klasse |
|---|---|---|---|
| `glow-crit` | Ring 1 px + Halo 24 px in Rot | Ring 1 px + nach unten versetzter roter Schatten | `shadow-glow-crit`, `.ng-glow-crit` |
| `glow-warn` | schwacher oranger Halo 14 px | weicher oranger Schatten | `shadow-glow-warn`, `.ng-glow-warn` |
| `glow-dot-crit` | Punkt-Halo 3 px + 10 px | nur 3-px-Ring | `shadow-glow-dot-crit` |
| `glow-dot-warn` | Punkt-Halo 7 px | 3-px-Ring | `shadow-glow-dot-warn` |
| `wire-glow` | `drop-shadow` rot | `none` | `[filter:var(--ng-wire-glow)]` |
| `branch-glow` | Ast-Schatten | `none` | `[box-shadow:var(--ng-branch-glow)]` |
| `ring-core` / `ring-core-warn` | innerer Schein Rot-Orange / Orange | 6 % Tönung | `HealthRing` |
| `accent-glow` | Akzent-Ring + Halo | 3-px-Akzent-Ring | `shadow-accent-glow` (nur Primär-Button) |
| `mark-halo`, `wm-glow` | 0.55, Leuchten im „o“ | 0.28, `none` | Logo |
| `breathe` | `ng-breathe 6s ease-in-out infinite` | `none` | `animate-breathe` |

### 2.3 Typografie

| Rolle | Schrift | Tailwind |
|---|---|---|
| UI / Fliesstext | Inter Tight 400/500/600 | `font-sans` (Default am `body`) |
| Display: Seitentitel, grosse Zahlen | Sora 500 (Zahlen) / 600 (Titel), Laufweite −0.03 … −0.045em | `font-display` |
| Mono: IPs, Regelnamen, Logs | JetBrains Mono 400/500 | `font-mono` |

Selbst gehostet aus den npm-Paketen `@fontsource-variable/{inter-tight,sora,jetbrains-mono}` über `next/font/local` (`src/app/fonts.ts`, `display: swap`) — der Build braucht kein Netz (kein `next/font/google` mehr). Pro Familie zwei Faces mit den Google-`unicode-range`s: latin (vorgeladen) und latin-ext (nur bei Bedarf geladen). Variablen `--font-sora(-ext)`, `--font-inter-tight(-ext)`, `--font-jetbrains-mono(-ext)`; die Tokens `--ng-font-*` reihen latin → latin-ext → Fallback-Stack. Inter (alt) ist entfernt.

Schriftgrössen in `rem` relativ zur Benutzer-Schriftgrösse (Default 14 px = `1rem`). Die Namen kollidieren nicht mit `text-xs/sm/base`:

| Klasse | px bei 14 px | Einsatz |
|---|---|---|
| `text-micro` | 11 | Badges, Achsen, Tab-Bar-Labels |
| `text-meta` | 12 | Metadaten, Labels, Tabellenkopf |
| `text-ui` | 13 | Buttons, Tabellenzellen, Listen |
| `text-body` | 14 | Fliesstext, Kartentitel |
| `text-lead` | 17 | Dialog-/Paneltitel, Abschnittstitel |
| `text-h3` | 20 | Unterüberschrift |
| `text-h1` | 24 | Seitentitel (Sora) |
| `text-num-sm` / `text-num` / `text-num-lg` | 28 / 36 / 40 | `BigNumber` (Sora) |

Zahlen: `.num` bzw. `tabular-nums` (Tabellenziffern; der `body` hat `tnum` bereits aktiv).

### 2.4 Radien, Abstände, Ebenen, Bewegung

| Token | Wert | Tailwind | Einsatz |
|---|---|---|---|
| `radius` | 10 px | `rounded-card` | Karten |
| `radius-lg` | 12 px | `rounded-ng-lg` | Dialog, Command Palette |
| `radius-ctl` | 8 px | `rounded-ctl` | Buttons, Felder |
| `radius-sm` | 7 px | `rounded-ng-sm` | kleine Buttons, Menüzeilen |
| `radius-chip` | 5 px | `rounded-chip` | Badge, Tag, Kbd |
| `radius-pill` | 9999 px | `rounded-pill` | Pills |
| `rail-w` / `topbar-h` / `tabbar-h` | 60 / 60 / 58 px | `w-rail`, `h-topbar`, `h-tabbar`, `pl-rail` | Shell |
| `content-max` | 1760 px | `max-w-content` | Inhaltsbreite |
| `gutter` / `gutter-sm` / `gap` | 24 / 16 / 16 px | `px-gutter` | Seitenrand, Kartenabstand |
| `z-*` | topbar 20, rail 30, scrim 60, popover 65, panel 70, palette 75, tooltip 80, toast 90 | `z-topbar` … | Ebenen |
| `dur-fast` / `dur` / `dur-panel`, `ease-out` | 120 / 150 / 220 ms, `cubic-bezier(.2,.7,.2,1)` | `ease-ng` | Übergänge |

### 2.5 Kontrast (WCAG 2.1 AA)

Geprüft durch `src/styles/tokens.test.ts` (schlägt bei Unterschreitung fehl). Durchscheinende Hintergründe (Soft-Töne) sind über `surface` gerechnet. Minimum 4.5 = Fliesstext, 3 = grosse Schrift/Grafik.

| Paar | Minimum | Dunkel | Hell |
|---|---|---|---|
| `text` auf `bg` / `surface` / `surface-2` / `surface-3` | 4.5 | 16.13 / 14.97 / 13.69 / 12.17 | 15.79 / 17.21 / 15.51 / 14.30 |
| `text-2` auf `bg` / `surface` / `surface-2` | 4.5 | 7.70 / 7.14 / 6.53 | 5.95 / 6.48 / 5.84 |
| `text-3` auf `bg` / `surface` / `surface-2` | 4.5 | 5.78 / 5.37 / 4.91 | 4.90 / 5.34 / 4.82 |
| `accent-text` auf `bg` / `surface` | 4.5 | 6.45 / 5.98 | 6.20 / 6.75 |
| `accent-text` auf `accent-soft` | 4.5 | 4.93 | 5.85 |
| `on-accent` auf `accent-btn` / `accent-btn-hover` | 4.5 | 5.03 / 5.90 | 5.95 / 7.28 |
| `tip-text` auf `tip-bg` | 4.5 | 12.51 | 14.84 |
| `st-ok-text` auf `surface` / `st-ok-soft` | 4.5 | 7.68 / 6.15 | 5.39 / 4.66 |
| `st-degraded-text` auf `surface` / Soft | 4.5 | 9.97 / 7.60 | 5.85 / 5.13 |
| `st-warning-text` auf `surface` / Soft | 4.5 | 7.41 / 5.97 | 5.23 / 4.52 |
| `st-down-text` auf `surface` / Soft | 4.5 | 5.92 / 5.10 | 5.63 / 4.67 |
| `st-maint-text` auf `surface` / Soft | 4.5 | 7.47 / 5.93 | 6.22 / 5.19 |
| `st-unknown-text` auf `surface` / Soft | 4.5 | 7.14 / 6.13 | 6.48 / 5.56 |
| Statusfarben (Grafik) auf `surface`: ok / degraded / warning / down / maint | 3 | 7.68 / 9.97 / 7.41 / 5.13 / 5.93 | 3.45 / 3.14 / 3.39 / 4.54 / 4.50 |
| `focus` auf `bg` / `surface` | 3 | 6.45 / 5.98 | 5.46 / 5.95 |

Bewusste Ausnahmen: `st-unknown` als Grafik liegt unter 3:1 („kein Licht = keine Daten“); Unbekannt wird deshalb immer zusätzlich über Form (hohl, schraffiert, gestrichelt) und Text vermittelt. Der Zähler-Badge an der Leiste (weiss auf `st-down`, 10 px) folgt E3 und ist `aria-hidden`; die Zahl steht im zugänglichen Namen des Links.

---

## 3. Tailwind-Klassen

### 3.1 Übersicht

| Zweck | Klassen |
|---|---|
| Flächen | `bg-bg`, `bg-rail`, `bg-surface`, `bg-surface-2`, `bg-surface-3`, `bg-hover`, `bg-scrim`, `bg-tip` |
| Ränder | `border-border`, `border-border-2`, `border-line`, `divide-border` |
| Text | `text-fg`, `text-fg-2`, `text-fg-3`, `text-accent`, `text-on-accent`, `text-tip-fg` |
| Akzent | `bg-accent`, `bg-accent-btn`, `hover:bg-accent-btn-hover`, `bg-accent-soft`, `border-accent/40` |
| Status | `bg-{ok,degraded,warning,down,maint}` (Grafik), `text-{…,unknown}` (AA-Textvariante), `bg-{…}-soft`, `border-{…}/30` |
| Opazität | funktioniert auf allen Farb-Tokens: `bg-down/20`, `border-accent/40` (über `color-mix`) |
| Schatten/Glow | `shadow-overlay`, `shadow-glow-crit`, `shadow-glow-warn`, `shadow-glow-dot-crit`, `shadow-glow-dot-warn`, `shadow-accent-glow`, `animate-breathe` |
| Hilfsklassen (CSS) | `.ng-glow-crit`, `.ng-glow-warn`, `.ng-glow-acked`, `.ng-hatch`, `.num`, `.ng-input`, `.ng-label`, `.ng-shimmer` |
| Schrift | `font-sans`, `font-display`, `font-mono`, `text-micro … text-num-lg` |
| Breakpoint Shell | `max-[759px]:` / `min-[760px]:` (unter 760 px: Tab-Bar statt Leiste) |

`text-ok` und `bg-ok` lösen bewusst auf **verschiedene** Tokens auf (`st-ok-text` bzw. `st-ok`), damit Text immer AA erfüllt. `cn()` (tailwind-merge) kennt die Schriftgrössen, Schatten und Radien.

### 3.2 Migrationstabelle `slate-*` / Hartcodes → semantisch

Die Migration ist abgeschlossen (Stand 10.10.2026); die Tabelle bleibt als Referenz, falls alter Code (Branches, Snippets) nachgezogen werden muss.

| Alt | Neu |
|---|---|
| `text-white`, `text-slate-50/100/200` | `text-fg` |
| `text-slate-300`, `text-slate-400` | `text-fg-2` |
| `text-slate-500`, `text-slate-600` | `text-fg-3` |
| `placeholder:text-slate-500/600` | (entfällt, global gesetzt) |
| `bg-slate-900`, `bg-[#111621]`, `var(--ng-surface)` (alt), `glass-card` | `bg-surface` (oder `<Card>`) |
| `bg-slate-800`, `bg-white/[0.04…0.06]`, `bg-white/5` | `bg-surface-2` |
| `bg-slate-700`, `bg-white/[0.08…0.14]`, `bg-white/10` | `bg-surface-3` |
| `hover:bg-white/[0.04…0.08]` | `hover:bg-surface-2` |
| `border-white/[0.06…0.08]`, `border-slate-800` | `border-border` |
| `border-white/[0.10…0.14]`, `border-slate-700` | `border-border-2` |
| `text-sky-400`, `text-violet-400`, `accent-text` | `text-accent` |
| `bg-sky-500` (Primärbutton) | `<Button>` bzw. `bg-accent-btn text-on-accent` |
| `bg-sky-500/20 text-sky-400` (aktiv) | `bg-accent-soft text-accent` |
| `text-emerald-400`, `text-green-400` | `text-ok` |
| `text-amber-400`, `text-yellow-400` | `text-degraded` (beeinträchtigt) bzw. `text-warning` (Warnung) — nach Bedeutung wählen |
| `text-orange-400` | `text-warning` |
| `text-red-400`, `text-rose-400` | `text-down` |
| `bg-emerald-500/20`, `bg-red-500/20 …` | `bg-ok-soft`, `bg-down-soft` … oder `<StatusPill>` |
| Wartung in Amber | `text-maint` / `bg-maint-soft` (Wartung ist nie Amber) |
| `bg-slate-500` als „unbekannt“ | `<StatusDot status="unknown">` (hohl) bzw. `.ng-hatch` |
| `text-[10px]`, `text-[11px]` | `text-micro` (11 px Minimum) |
| `text-xs` | `text-meta` |
| `text-sm` | `text-ui` (13) oder `text-body` (14) |
| `text-2xl/3xl font-bold` (Seitentitel) | `<PageHeader>` |
| `text-[10px] uppercase tracking-wider` (Widget-Titel) | `<CardHeader title>` (14 px, normal geschrieben) |
| `font-mono` für Zahlen | `.num` (Tabellenziffern in der UI-Schrift); `font-mono` nur für IPs, IDs, Logs |
| `animate-pulse` auf Statuspunkten | `StatusDot breathe` nur bei kritisch + unquittiert, sonst nichts |
| `style={{ color: 'var(--ng-text-primary)' }}` | `className="text-fg"` |
| `ngColors.critical` (ECharts) | `useChartTheme().status.down` |
| `GlassCard` | `Card` |
| eigene Tab-Leisten (`useState<Tab>`) | `Tabs` + `TabPanel` (Panels) oder `NavTabs` (URLs) |
| `<label>` + `<input className="ng-input">` ohne `htmlFor` | `<Field label><Input/></Field>` |

Übergangsschicht — **erledigt (10.10.2026)**: Die „Legacy bridge“ am Ende von `globals.css` (Light-Mode-Abbildung der `slate`/`white/x`-Klassen), die Alias-Tokens in `tokens.json → alias` (`--ng-text-primary`, `--ng-card-bg`, `--ng-glass-*` …), der Export `ngColors`, die Hilfsklassen `.glass-card`, `.glass-elevated`, `.nav-active`, `.accent-text`, `.accent-bg`, `.stat-card-hover` und die Komponente `GlassCard` sind gelöscht. `slate-*`, `white/x`, Hex-Werte und die alten Variablennamen funktionieren damit nicht mehr (im Light Mode sähen sie falsch aus) — nur noch die semantischen Klassen verwenden.

---

## 4. Komponentenkatalog (`src/components/ui`)

| Komponente | Datei | Zweck und Regeln |
|---|---|---|
| `Button`, `IconButton`, `buttonClasses()` | `Button.tsx` | Varianten `primary` (eine pro Ansicht, einziger Akzent-Glow), `secondary`, `ghost` (Toolbars), `danger` (getönt, kein roter Block). Grössen `sm` 28 / `md` 36 / `lg` 40 px, `loading` (Spinner, `aria-busy`, deaktiviert). `IconButton` verlangt `aria-label`. `buttonClasses()` für `<Link>`. |
| `Card`, `CardHeader` | `Card.tsx` | Flache Fläche, 1 px Rand, 10 px Radius, Padding 20 (mobil 16). `glow="crit"|"warn"` nur für Objekte mit Handlungsbedarf, `acknowledged` halbiert den Halo und stoppt das Atmen. `CardHeader` = Titel 14 px + `meta` + `actions`, `titleId` für `aria-labelledby`. |
| `StatusDot` | `StatusDot.tsx` | 6/8/10 px. Zustände `ok degraded warning down maint unknown` (+ alte Namen `online offline error maintenance disabled`). Unknown = hohler Ring. Down/Warning glühen (abschaltbar), `breathe` nur für kritisch + unquittiert. Hat einen zugänglichen Namen (`label`, `""` wenn Text daneben steht). |
| `StatusPill` | `StatusPill.tsx` | Getönte Pill mit Punkt und AA-Text, Default-Label aus dem Vokabular. Für Zusammenfassungen („1 critical“, „9 no data“) und Statusspalten. Pills glühen nie. |
| `Badge` | `Badge.tsx` | Kleines Rechteck für Zähler/Typen; `tone` (`neutral accent ok …`) oder `variant="severity"`. Integrationsfarben entfallen. |
| `Tag`, `Pill` | `Tag.tsx` | `Tag` = Outline-Label; `planned` = gestrichelt („Multi-tenant · Planned“). `Pill` neutral/akzent für Filter und Auswahl. |
| `Tabs`, `TabPanel`, `NavTabs`, `SegmentedControl` | `Tabs.tsx` | `Tabs` = ARIA-Tablist mit Pfeiltasten (Panels in einer Seite). `NavTabs` = Link-Tabs mit `aria-current` (Sub-Navigation, Detail-Tabs als Pfadsegment). `SegmentedControl` = Radiogroup für 2–5 Ansichtsoptionen. Zähler mit `count`/`countTone`. |
| `Field`, `Input`, `Select`, `Textarea`, `Checkbox`, `Switch` | `Field.tsx` | `Field` verdrahtet Label, Hinweis, Fehler (`htmlFor`, `aria-describedby`, `aria-invalid`). `Switch` = `role="switch"` für sofort wirksame Einstellungen; `Checkbox` in Formularen mit Speichern. Alle nutzen `.ng-input`. |
| `TableContainer`, `Table`, `THead`, `TBody`, `Tr`, `Th`, `Td` | `Table.tsx` | `density` comfortable 36 / compact 28 px, `THead sticky`, `Th sort/onSort` setzt `aria-sort`, `numeric` = rechtsbündig + Tabellenziffern, `Tr interactive/selected`. Statusspalte zuerst. |
| `SidePanel`, `PanelSection` | `SidePanel.tsx` | Drawer rechts (460 / 640 px, mobil voll), Fokusfalle, Esc, `inert` auf dem Rest, Fokus zurück zum Auslöser. Kopf: `icon`, `title`, `meta` (Zustand · Quelle · Frische), `footer` mit Aktionen. Nie zwei übereinander. |
| `Modal` | `Modal.tsx` | Zentrierter Dialog für kurze, blockierende Entscheidungen (`ConfirmDialog` baut darauf). Gleiche Fokus-/Esc-/Inert-Logik (`hooks/useModalBehavior.ts`), Portal unter `<body>`, `size`, `description`, `footer`. |
| `Popover`, `PopoverItem` | `Popover.tsx` | Kleines nicht-modales Fenster (About, Tenant, Konto). Esc, Klick ausserhalb, Fokus hinein und zurück. |
| `Tooltip` | `Tooltip.tsx` | Hover + Tastaturfokus, Esc schliesst, `aria-describedby` (oder `asLabel` = Name für Icon-Links). Nie einzige Quelle wichtiger Information. |
| `ToastContainer` | `Toast.tsx` | Dunkle Fläche unten Mitte (mobil über der Tab-Bar), `aria-live`; Fehler als `role="alert"`. API unverändert: `useToastStore().show(msg, type)`. |
| `Skeleton` | `Skeleton.tsx` | Platzhalter in Form des Inhalts; nie Nullen während des Ladens. |
| `EmptyState` | `EmptyState.tsx` | `variant`: `not-configured` (Einrichten anbieten), `no-results` (Filter zurücksetzen), `confirmed` (grün, **nur mit `asOf`-Zeitstempel**). |
| `QueryState`, `QueryErrorState`, `StaleDataBanner`, `formatAsOf` | `QueryState.tsx` | Lade-/Fehler-/Leer-/Datenzweig einer Query. Fehlgeschlagener Refetch mit Cache → Daten bleiben, gelbes Banner „Showing data from 14:32. Refresh failed …“ (`dataUpdatedAt` mitgeben). 403 → „No access“ ohne Retry. |
| `Kbd` | `Kbd.tsx` | Tastenkappe. |
| `BigNumber` | `BigNumber.tsx` | Kennzahl in Sora mit Tabellenziffern, `unit`, `label`, `state` (nur wenn die Zahl der Zustand ist), `stale`. `null` → „—“ in Grau, nie 0. |
| `HealthRing`, `HealthLegend` | `HealthRing.tsx` | Donut für Objektzustände (feste Reihenfolge ok → unknown), Unbekannt schraffiert, Mitte (`center`) mit innerem Schein nach Gesamtlage (kritisch Rot-Orange, Warnung Orange, sonst nichts), atmet nur dunkel. `aria-label` automatisch („48 hosts: 29 ok, …“). |
| `NodeglowMark`, `Wordmark`, `Lockup` | `NodeglowMark.tsx` | Zeichen aus E3 (Knoten, Halo, Orbit, Satellit), `mono` für einfarbig, Wortmarke mit leuchtendem „o“. |
| `CommandPalette` / `CommandPaletteHost` | | Ctrl/Cmd+K oder Suchknopf in der Kopfzeile (`useUiStore().setPaletteOpen`). Seiten aus der Navigations-Registry, Hosts, Integrationen, Aktionen. |
| `KeyboardShortcuts` | | `g` + Taste aus der Registry (`g d` Overview, `g n`/`g a` Incidents, `g r` Regeln, `g h` Hosts, `g o` Topologie, `g l`/`g s` Logs, `g i` Settings, `g t` Systemstatus), `?` Hilfe. |

Hilfen: `lib/status.ts` (Vokabular, `toHealthState`, `glows`, `STATE_FILL/TEXT/SOFT/VAR`, `describeSegments`), `lib/chart-theme.ts` (`useChartTheme`, `buildEChartsTheme`, `readToken`, `resolveCssColor`), `lib/navigation.ts` (Registry, `findActive`, `visibleSections`, `shortcutRoutes`).

### Charts

`components/charts/EChart` baut das ECharts-Theme zur Laufzeit aus den CSS-Tokens (`buildEChartsTheme`) und initialisiert neu, sobald `data-theme` wechselt. Farben pro Reihe: `const t = useChartTheme(); color: t.status.down` bzw. `t.series[0]`; Flächen `areaStyle: { color: t.accentFill }`. Statusreihen nur in Statusfarben, alles andere aus `series`. `ariaLabel` setzen (Kurzbeschreibung des Verlaufs).

---

## 5. Glow-Regeln

1. **Nur Kritisch und Warnung leuchten.** Kritisch stärker (`glow-crit`, `glow-dot-crit`), Warnung schwächer und statisch (`glow-warn`, `glow-dot-warn`).
2. **Gesund, Wartung und Unbekannt leuchten nie** — auch nicht auf Hover. „Kein Licht = keine Daten“.
3. **Atmen** (`animate-breathe`, 6 s, Deckkraft 0.62 → 1) nur für kritische, *unquittierte* Halos. Aus bei `prefers-reduced-motion` (global) und im Light Mode (Token `breathe: none`). Quittiert → `.ng-glow-acked` (halber Halo, kein Atmen).
4. **Akzent-Glow** nur am Primär-Button (Hover/Fokus) und im Logo.
5. **Light Mode:** Glow ist getönter, nach unten versetzter Schatten, nie Licht auf Weiss; Leitungen ohne Glow. Das regeln die Tokens — Komponenten verwenden immer die Tokens, nie eigene `box-shadow`-Farben.
6. Glow sitzt am **Objekt**, das Aufmerksamkeit braucht (Incident-Icon, Knoten, Zähler), nicht an Pills, Überschriften oder ganzen Seitenbereichen. Höchstens eine glühende Karte pro Kartenreihe.

## 6. Zustandskommunikation

Grundregel (IA §6.5): **Fehlende, veraltete oder unbeobachtete Daten sehen nie gesund aus.** Grün nur für positiv bestätigte, frische Messungen.

| Zustand | Darstellung im System |
|---|---|
| Lädt (erstmals) | `Skeleton` in Form des Inhalts; keine Nullen |
| Aktualisiert im Hintergrund | Inhalt bleibt; höchstens dezenter Hinweis im Kopf |
| Leer, nicht eingerichtet / keine Treffer | `EmptyState variant="not-configured" | "no-results"` (neutral) |
| Leer, bestätigt gut | `EmptyState variant="confirmed" asOf="14:32:05"` (grün, mit Zeit) |
| Keine Daten von der Quelle | „—“ in `text-fg-3` (`BigNumber value={null}`), Tooltip mit Quelle |
| Unbekannt / unbeobachtet | `StatusDot status="unknown"` (hohl), `StatusPill status="unknown"` („No data“), Flächen `.ng-hatch`, gestrichelte Ränder; nie grün, nie Glow |
| Veraltet (stale) | `StaleDataBanner` mit Alter, Wert `stale` (gedimmt) |
| Fehler | `QueryErrorState` mit Grund und „Retry“ |
| Verbindung getrennt | Kopfzeile: gelbe Pill „Live paused since 14:31 · polling“ (Klick = neu verbinden); verbunden = ruhiges „Live“ ohne Farbe/Glow; erster Versuch = „Connecting…“ (hohl) |
| Keine Berechtigung | `QueryErrorState` mit Schloss, ohne Retry |
| Wartung | `maint` (grau-blau, nie Amber) + Fensterende im Text |
| Deaktiviert | `StatusDot status="disabled"` (gedimmt) + Text |

Jeder Zustand hat **Farbe und Form und Text**. Status nie nur über Farbe.

## 7. App-Shell, Layout, Raster

- **Leiste** (≥ 760 px): 60 px, Zeichen oben (About-Popover), Bereichs-Icons mit Tooltip, aktiv = `accent-soft`, Incident-Zähler mit Glow, Administration unten.
- **Kopfzeile**: 60 px, sticky. Tenant („Default tenant“, Tag *Multi-tenant · Planned*, Popover erklärt den Status), Suche (Ctrl K; unter 900 px nur Icon), Live-Status, Glow (nur bei aktiver KI), Theme-Umschalter, Konto (Theme dunkel/hell/System, Tastenkürzel, Abmelden).
- **Sub-Navigation**: `NavTabs` oben im Inhalt, aus `lib/navigation.ts`, nur bei Bereichen mit ≥ 2 Seiten; Zähler (Incidents rot, Discovery neutral).
- **Mobil (< 760 px)**: Tab-Bar unten (58 px) mit 5 Bereichen + „More“ (SidePanel mit allen Seiten). Inhalt `px-4`, unten 90 px Platz.
- **Inhalt**: `max-w-content` (1760 px), Seitenrand 24 px (mobil 16), oben 28 px. Kartenraster: 12 Spalten, `gap-4`-Äquivalent 16 px (`grid grid-cols-12 gap-[16px]`); unter 1200 px 2 Spalten, unter 760 px 1 Spalte (E3-Raster als Vorlage).
- Skip-Link „Skip to content“ springt auf `#main`.

### Navigations-Mapping (alle bestehenden Routen bleiben unverändert erreichbar)

| Bereich (Leiste) | Sub-Navigation → Route |
|---|---|
| Overview | `/` |
| Incidents & Alerting | Incidents → `/alerts` (inkl. `/incidents/[id]`) · Maintenance → `/alerts?tab=maintenance` · Alert rules → `/rules` |
| Infrastructure | Hosts → `/hosts` (+ `/hosts/[id]`) · Topology → `/topology` · Agents & Probes → `/agents` (+ `/agents/[id]`) · Integrations → `/integration/store` (+ `/integration/[type]`, `/integration/[type]/[id]`) · SNMP → `/snmp` · Discovery → `/tasks` · Scans → `/scanner` · Certificates → `/ssl` · Backups → `/backups` |
| Observability | Logs → `/syslog` · Log overview → `/syslog/dashboard` · Log patterns → `/syslog/templates` · Traffic → `/bandwidth` |
| Analytics | Reports → `/digest` |
| Administration | Settings → `/settings`* · Users → `/users`* · Credentials → `/credentials` · System status → `/system/status` · Audit log → `/system/audit`* |

\* nur Admin. `src/lib/navigation.test.ts` scannt `app/(app)/**/page.tsx` und schlägt fehl, wenn eine Route keinem Eintrag zugeordnet ist. Die Ziel-URLs der IA (`/incidents`, `/logs`, `/admin/*` …) kommen später mit Weiterleitungen; dann nur `href`/`match` in der Registry anpassen.

---

## 8. Seite migrieren — Checkliste für Phase F

1. **Kopf**: `PageHeader` (Titel, eine Zeile Beschreibung mit Frische, max. eine Primäraktion). Keine eigene Tab-Leiste für Bereichsnavigation — die Shell zeigt die Sub-Navigation.
2. **Flächen**: Inline-`style`/eigene Kartenflächen → `Card` + `CardHeader`. Kein `backdrop-blur`, keine Verläufe, keine Schatten auf Karten.
3. **Farben**: alle `slate-*`, `white/x`, `sky/emerald/red/amber-*`, Hex-Werte und `var(--ng-…)`-Inline-Styles nach Tabelle 3.2 ersetzen. Suche: `rg "slate-|white/|sky-|emerald-|amber-|red-|#[0-9A-Fa-f]{6}|style=\{\{" <datei>`.
4. **Status**: über `lib/status.ts` mappen und `StatusDot`/`StatusPill` verwenden. Unbekannt nie grün, Wartung nie Amber. Glow nur über die Komponenten bzw. `Card glow`.
5. **Typo**: `text-micro … text-h1`; Zahlen `.num`/`BigNumber`; Mono nur für technische Bezeichner. Nichts unter 11 px.
6. **Zustände**: `QueryState` (inkl. `dataUpdatedAt`) bzw. `QueryErrorState`/`StaleDataBanner`; `EmptyState` mit passender `variant`.
7. **Formulare**: `Field` + `Input/Select/Textarea`, `Switch` mit `role="switch"`, `Checkbox`. Jede Eingabe hat ein Label.
8. **Tabs/Tabellen**: `Tabs`/`TabPanel` bzw. `NavTabs`; Tabellen mit `Table`-Primitives (Status zuerst, Zahlen rechts, `aria-sort`).
9. **Dialoge**: Details/Vorschau → `SidePanel`; kurze Bestätigung → `Modal`/`ConfirmDialog`. Keine eigenen Overlays.
10. **Charts**: `EChart` + `useChartTheme()`; keine Farblisten im Code, `ariaLabel` setzen.
11. **A11y**: sichtbarer Fokus nicht entfernen, Icon-Buttons mit `aria-label`, Bewegung nur bei Zustandswechsel.
12. **Prüfen**: dunkel **und** hell, 1440 px und 400 px (kein horizontales Scrollen der Seite), `npx tsc --noEmit`, `npm run lint`, `npx vitest run`, `npm run build`.

Alle Seiten sind migriert; Legacy-Bridge, `tokens.json → alias` und `GlassCard` sind entfernt (siehe 3.2).
