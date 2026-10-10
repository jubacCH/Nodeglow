'use client';

import { useRef, useEffect } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, GaugeChart as EGaugeChart } from 'echarts/charts';
import {
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { buildEChartsTheme, useChartTheme } from '@/lib/chart-theme';

echarts.use([
  BarChart, LineChart, PieChart, EGaugeChart,
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, CanvasRenderer,
]);

export interface EChartProps {
  option: EChartsOption;
  className?: string;
  height?: number | string;
  /** Accessible description of what the chart shows (role="img"). */
  ariaLabel?: string;
}

/**
 * ECharts with the Nodeglow theme. The theme is built from the live CSS
 * tokens (lib/chart-theme) and the chart is re-initialised when the theme
 * changes, so it always matches dark/light. Per-series colours: use
 * useChartTheme().status.* / .series[] instead of hex values.
 */
export function EChart({ option, className, height = 200, ariaLabel }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const optionRef = useRef(option);
  optionRef.current = option;
  const tokens = useChartTheme();

  // Init / re-init when the theme changes.
  useEffect(() => {
    if (!ref.current) return;
    chartRef.current?.dispose();
    chartRef.current = echarts.init(ref.current, buildEChartsTheme(tokens));
    chartRef.current.setOption(optionRef.current, true);
    const obs = new ResizeObserver(() => chartRef.current?.resize());
    obs.observe(ref.current);
    return () => {
      obs.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [tokens]);

  // Update option when it changes. Merge mode (no notMerge) lets ECharts
  // diff series data instead of rebuilding the whole chart on every refetch.
  useEffect(() => {
    chartRef.current?.setOption(option);
  }, [option]);

  return (
    <div
      ref={ref}
      className={className}
      style={{ height }}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
    />
  );
}
