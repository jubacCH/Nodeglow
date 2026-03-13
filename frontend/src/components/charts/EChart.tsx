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
import { nodeglowTheme } from '@/lib/echarts-theme';

echarts.use([
  BarChart, LineChart, PieChart, EGaugeChart,
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, CanvasRenderer,
]);

// Register theme once
echarts.registerTheme('nodeglow', nodeglowTheme);

interface EChartProps {
  option: EChartsOption;
  className?: string;
  height?: number | string;
}

export function EChart({ option, className, height = 200 }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current = echarts.init(ref.current, 'nodeglow');
    const obs = new ResizeObserver(() => chartRef.current?.resize());
    obs.observe(ref.current);
    return () => {
      obs.disconnect();
      chartRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={className} style={{ height }} />;
}
