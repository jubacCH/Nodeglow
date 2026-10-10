'use client';

import dynamic from 'next/dynamic';
import type { EChartProps } from './EChart';

/**
 * The one way pages should render an ECharts chart. echarts is several
 * hundred kB, so it is split into its own chunk and loaded on the client
 * only when a chart actually mounts. The wrapper reserves the chart's
 * height while the chunk loads so the layout does not jump.
 */
const EChartImpl = dynamic(
  () => import('./EChart').then((m) => ({ default: m.EChart })),
  { ssr: false, loading: () => null },
);

export function EChart(props: EChartProps) {
  return (
    <div style={{ minHeight: props.height ?? 200 }}>
      <EChartImpl {...props} />
    </div>
  );
}
