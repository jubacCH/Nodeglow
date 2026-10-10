import type { Config } from 'tailwindcss';

/**
 * Semantic design-system classes. Every colour points at a CSS variable from
 * design-tokens/tokens.json (generated into src/styles/tokens.gen.css), so the
 * same class works in dark and light. Opacity modifiers work too
 * (`bg-down/20`, `border-accent/40`) via color-mix.
 *
 * Migration table slate-* -> semantic: docs/design/04-design-system.md.
 */
const v = (name: string) =>
  `color-mix(in srgb, var(--ng-${name}) calc(<alpha-value> * 100%), transparent)`;

/** Graphic colours (fills, borders, dots, bars). */
const status = (name: string) => ({
  DEFAULT: v(`st-${name}`),
  soft: `var(--ng-st-${name}-soft)`,
  text: v(`st-${name}-text`),
});

const colors = {
  bg: v('bg'),
  rail: v('rail'),
  surface: { DEFAULT: v('surface'), 2: v('surface-2'), 3: v('surface-3') },
  border: { DEFAULT: v('border'), 2: v('border-2') },
  line: v('line'),
  hover: 'var(--ng-hover)',
  fg: { DEFAULT: v('text'), 2: v('text-2'), 3: v('text-3') },
  accent: {
    DEFAULT: v('accent'),
    text: v('accent-text'),
    hover: v('accent-hover'),
    btn: v('accent-btn'),
    'btn-hover': v('accent-btn-hover'),
    soft: 'var(--ng-accent-soft)',
  },
  'on-accent': v('on-accent'),
  focus: v('focus'),
  ok: status('ok'),
  degraded: status('degraded'),
  warning: status('warning'),
  down: status('down'),
  maint: status('maint'),
  unknown: status('unknown'),
  hatch: v('hatch-2'),
  grid: 'var(--ng-grid)',
  scrim: 'var(--ng-scrim)',
  tip: { DEFAULT: v('tip-bg'), fg: v('tip-text') },
};

/** `text-ok`, `text-accent` … resolve to the AA-checked text variants. */
const textOverrides = {
  ok: { ...status('ok'), DEFAULT: v('st-ok-text') },
  degraded: { ...status('degraded'), DEFAULT: v('st-degraded-text') },
  warning: { ...status('warning'), DEFAULT: v('st-warning-text') },
  down: { ...status('down'), DEFAULT: v('st-down-text') },
  maint: { ...status('maint'), DEFAULT: v('st-maint-text') },
  unknown: { ...status('unknown'), DEFAULT: v('st-unknown-text') },
  accent: { ...colors.accent, DEFAULT: v('accent-text') },
};

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors,
      textColor: textOverrides,
      // Type scale (rem, 1rem = user font size, default 14px). Names do not
      // collide with Tailwind's xs/sm/base so unmigrated pages keep working.
      fontSize: {
        micro: ['var(--ng-fs-micro)', { lineHeight: '1.35' }],
        meta: ['var(--ng-fs-meta)', { lineHeight: '1.4' }],
        ui: ['var(--ng-fs-ui)', { lineHeight: '1.45' }],
        body: ['var(--ng-fs-body)', { lineHeight: '1.45' }],
        lead: ['var(--ng-fs-lead)', { lineHeight: '1.3' }],
        h3: ['var(--ng-fs-h3)', { lineHeight: '1.25' }],
        h1: ['var(--ng-fs-h1)', { lineHeight: '1.2' }],
        'num-sm': ['var(--ng-fs-num-sm)', { lineHeight: '1.1' }],
        num: ['var(--ng-fs-num)', { lineHeight: '1.05' }],
        'num-lg': ['var(--ng-fs-num-lg)', { lineHeight: '1.05' }],
      },
      fontFamily: {
        sans: ['var(--ng-font-ui)'],
        display: ['var(--ng-font-display)'],
        mono: ['var(--ng-font-mono)'],
      },
      borderRadius: {
        card: 'var(--ng-radius)',
        ctl: 'var(--ng-radius-ctl)',
        chip: 'var(--ng-radius-chip)',
        'ng-sm': 'var(--ng-radius-sm)',
        'ng-lg': 'var(--ng-radius-lg)',
        pill: 'var(--ng-radius-pill)',
      },
      boxShadow: {
        overlay: 'var(--ng-shadow)',
        'ng-sm': 'var(--ng-shadow-sm)',
        'glow-crit': 'var(--ng-glow-crit)',
        'glow-warn': 'var(--ng-glow-warn)',
        'glow-dot-crit': 'var(--ng-glow-dot-crit)',
        'glow-dot-warn': 'var(--ng-glow-dot-warn)',
        'accent-glow': 'var(--ng-accent-glow)',
      },
      animation: {
        breathe: 'var(--ng-breathe)',
      },
      spacing: {
        rail: 'var(--ng-rail-w)',
        topbar: 'var(--ng-topbar-h)',
        tabbar: 'var(--ng-tabbar-h)',
        gutter: 'var(--ng-gutter)',
      },
      maxWidth: {
        content: 'var(--ng-content-max)',
      },
      zIndex: {
        topbar: '20',
        rail: '30',
        scrim: '60',
        popover: '65',
        panel: '70',
        palette: '75',
        tooltip: '80',
        toast: '90',
      },
      transitionTimingFunction: {
        ng: 'var(--ng-ease-out)',
      },
      // Shell breakpoint: below 760px the rail becomes a bottom tab bar. Use
      // the arbitrary variants `max-[759px]:` / `min-[760px]:` for it, so the
      // default sm/md/lg ordering stays intact.
    },
  },
  plugins: [],
};
export default config;
