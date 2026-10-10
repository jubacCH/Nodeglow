'use client';

import { useMemo } from 'react';
import { readToken, readTokenRaw, resolveCssColor } from '@/lib/chart-theme';
import type { HealthState } from '@/lib/status';
import type { ResolvedTheme } from '@/lib/theme';
import { useResolvedTheme } from '@/lib/useResolvedTheme';
import type { NgToken } from '@/styles/tokens.gen';

/**
 * Canvas colours for the topology map, resolved from the design tokens
 * (canvas cannot read CSS variables). Re-read whenever the theme changes, so
 * the map follows dark/light like the rest of the UI. No hex values here.
 */
export interface TopologyPalette {
  theme: ResolvedTheme;
  surface: string;
  node: string;
  nodeHover: string;
  border: string;
  borderHover: string;
  line: string;
  text: string;
  text2: string;
  text3: string;
  hatchBg: string;
  /** Dot / wire colour per state. */
  state: Record<HealthState, string>;
  /** AA text colour per state. */
  stateText: Record<HealthState, string>;
  /** Tinted fill and border for a down node (E3 `.tcol.bad > .node`). */
  downFill: string;
  downBorder: string;
  /** Halo colour for critical nodes: light in dark mode, a soft tinted shadow in light mode. */
  glow: string;
  fontSans: string;
  fontMono: string;
}

export function readTopologyPalette(theme: ResolvedTheme): TopologyPalette {
  const states: HealthState[] = ['ok', 'degraded', 'warning', 'down', 'maint', 'unknown'];
  const state = Object.fromEntries(states.map((s) => [s, readToken(`st-${s}` as NgToken)])) as Record<HealthState, string>;
  const stateText = Object.fromEntries(states.map((s) => [s, readToken(`st-${s}-text` as NgToken)])) as Record<HealthState, string>;
  const fontSans = typeof document !== 'undefined' ? getComputedStyle(document.body).fontFamily : 'sans-serif';
  return {
    theme,
    surface: readToken('surface'),
    node: readToken('surface-2'),
    nodeHover: readToken('surface-3'),
    border: readToken('border'),
    borderHover: readToken('border-2'),
    line: readToken('line'),
    text: readToken('text'),
    text2: readToken('text-2'),
    text3: readToken('text-3'),
    hatchBg: readToken('hatch-2'),
    state,
    stateText,
    downFill: resolveCssColor('color-mix(in srgb, var(--ng-st-down) 9%, var(--ng-surface-2))'),
    downBorder: resolveCssColor('color-mix(in srgb, var(--ng-st-down) 60%, transparent)'),
    glow: resolveCssColor(`color-mix(in srgb, var(--ng-st-down) ${theme === 'dark' ? 60 : 28}%, transparent)`),
    fontSans,
    fontMono: readTokenRaw('font-mono') || 'monospace',
  };
}

/** Theme-aware palette; recomputed when `data-theme` changes. */
export function useTopologyPalette(): TopologyPalette {
  const theme = useResolvedTheme();
  return useMemo(() => readTopologyPalette(theme), [theme]);
}
