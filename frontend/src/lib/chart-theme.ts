/**
 * ECharts theme built from the live CSS design tokens, so charts follow the
 * active theme (dark/light) and any future token change without a second
 * palette in JS. Canvas cannot read CSS variables, therefore tokens are
 * resolved to concrete rgba() strings at runtime.
 *
 *   const t = useChartTheme();        // re-renders on theme change
 *   series: [{ type: 'line', color: t.status.down }]
 *
 * <EChart> applies buildEChartsTheme() automatically and re-inits on theme
 * change; pages only need useChartTheme() for per-series colours.
 */
import { useMemo } from 'react';
import type { ResolvedTheme } from './theme';
import { useResolvedTheme } from './useResolvedTheme';
import type { NgToken } from '@/styles/tokens.gen';

let probe: HTMLSpanElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;

/** Resolve any CSS colour expression (incl. var() and color-mix) to rgba(). */
export function resolveCssColor(value: string): string {
  if (typeof document === 'undefined') return value;
  if (!probe) {
    probe = document.createElement('span');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none';
    document.body.appendChild(probe);
  }
  probe.style.color = '';
  probe.style.color = value;
  const computed = getComputedStyle(probe).color;
  // Normalise modern syntaxes (color(srgb …), oklab, …) through a 1px canvas.
  if (/^rgba?\(/.test(computed)) return computed;
  if (!ctx) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    ctx = canvas.getContext('2d', { willReadFrequently: true });
  }
  if (!ctx) return computed;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = computed;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${Math.round((a / 255) * 1000) / 1000})`;
}

/** Current value of a colour token (`--ng-<name>`) as rgba(). */
export function readToken(name: NgToken): string {
  return resolveCssColor(`var(--ng-${name})`);
}

/** Current value of a non-colour token (font stack, radius …). */
export function readTokenRaw(name: NgToken): string {
  if (typeof document === 'undefined') return '';
  return getComputedStyle(document.documentElement).getPropertyValue(`--ng-${name}`).trim();
}

export interface ChartTokens {
  theme: ResolvedTheme;
  text: string;
  text2: string;
  text3: string;
  surface: string;
  border: string;
  line: string;
  grid: string;
  accent: string;
  accentFill: string;
  tipBg: string;
  tipText: string;
  fontFamily: string;
  monoFamily: string;
  /** Categorical palette (series without semantic meaning). */
  series: string[];
  /** Status colours for series that carry a state. Unknown is never green. */
  status: { ok: string; degraded: string; warning: string; down: string; maint: string; unknown: string };
}

export function readChartTokens(theme: ResolvedTheme): ChartTokens {
  const fontFamily = typeof document !== 'undefined' ? getComputedStyle(document.body).fontFamily : 'sans-serif';
  return {
    theme,
    text: readToken('text'),
    text2: readToken('text-2'),
    text3: readToken('text-3'),
    surface: readToken('surface'),
    border: readToken('border'),
    line: readToken('line'),
    grid: readToken('grid'),
    accent: readToken('accent'),
    accentFill: readToken('chart-fill'),
    tipBg: readToken('tip-bg'),
    tipText: readToken('tip-text'),
    fontFamily,
    monoFamily: readTokenRaw('font-mono') || 'monospace',
    series: (['chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6'] as const).map(readToken),
    status: {
      ok: readToken('st-ok'),
      degraded: readToken('st-degraded'),
      warning: readToken('st-warning'),
      down: readToken('st-down'),
      maint: readToken('st-maint'),
      unknown: readToken('st-unknown'),
    },
  };
}

/** Theme-aware chart tokens; recomputed when the theme changes. */
export function useChartTheme(): ChartTokens {
  const theme = useResolvedTheme();
  return useMemo(() => readChartTokens(theme), [theme]);
}

/** ECharts theme object (pass to echarts.init(el, theme)). */
export function buildEChartsTheme(t: ChartTokens) {
  const axisCommon = {
    axisLine: { show: false, lineStyle: { color: t.line } },
    axisTick: { show: false },
    axisLabel: { color: t.text3, fontSize: 11 },
    splitLine: { lineStyle: { color: t.grid } },
  };
  return {
    backgroundColor: 'transparent',
    color: t.series,
    textStyle: { fontFamily: t.fontFamily, fontSize: 12, color: t.text2 },
    title: { textStyle: { color: t.text, fontSize: 14, fontWeight: 500 } },
    legend: { textStyle: { color: t.text2 }, inactiveColor: t.text3, icon: 'roundRect', itemWidth: 10, itemHeight: 10 },
    grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
    categoryAxis: { ...axisCommon, axisLine: { show: true, lineStyle: { color: t.line } }, splitLine: { show: false } },
    valueAxis: axisCommon,
    timeAxis: { ...axisCommon, axisLine: { show: true, lineStyle: { color: t.line } }, splitLine: { show: false } },
    line: { smooth: false, symbol: 'none', symbolSize: 4, lineStyle: { width: 2 }, areaStyle: { opacity: 0.12 } },
    bar: { barMaxWidth: 24, itemStyle: { borderRadius: [2, 2, 0, 0] } },
    pie: { itemStyle: { borderColor: t.surface, borderWidth: 2 } },
    gauge: {
      axisLine: { lineStyle: { color: [[1, t.grid]] } },
      progress: { itemStyle: { color: t.accent } },
      detail: { color: t.text },
    },
    tooltip: {
      backgroundColor: t.tipBg,
      borderWidth: 0,
      padding: [8, 10],
      textStyle: { color: t.tipText, fontSize: 12 },
      extraCssText: 'border-radius: 8px; box-shadow: var(--ng-shadow);',
      axisPointer: { lineStyle: { color: t.text3 }, crossStyle: { color: t.text3 } },
    },
    dataZoom: { textStyle: { color: t.text3 }, borderColor: t.border, fillerColor: t.accentFill },
  };
}
