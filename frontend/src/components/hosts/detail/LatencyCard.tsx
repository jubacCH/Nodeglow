'use client';

import { useMemo, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { EChart } from '@/components/charts/LazyEChart';
import { useHostHistory } from '@/hooks/queries/useHosts';
import { useChartTheme } from '@/lib/chart-theme';
import type { PingResult } from '@/types';
import { withGaps } from './latency';

type Range = '1' | '6' | '24';
const RANGES: { value: Range; label: string }[] = [
  { value: '1', label: '1h' },
  { value: '6', label: '6h' },
  { value: '24', label: '24h' },
];

function LatencyChart({ results, threshold }: { results: PingResult[]; threshold: number | null }) {
  const t = useChartTheme();
  const points = useMemo(() => withGaps(results), [results]);
  const failed = points.filter((p) => !p.ok).length;
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter(params: unknown) {
        const list = params as Array<{ seriesName: string; value: [number, number | null] }>;
        const first = list[0];
        if (!first) return '';
        const time = new Date(first.value[0]).toLocaleString();
        const lat = list.find((p) => p.seriesName === 'Latency')?.value[1];
        const fail = list.some((p) => p.seriesName === 'Failed check');
        return `${time}<br/>${fail ? 'Check failed' : lat == null ? 'No data' : `Latency ${lat.toFixed(1)} ms`}`;
      },
    },
    // Axis runs to now, so a period without data at the end stays visible.
    xAxis: { type: 'time', max: Date.now() },
    yAxis: { type: 'value', name: 'ms', nameTextStyle: { color: t.text3 }, min: 0 },
    series: [
      {
        name: 'Latency',
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        color: t.accent,
        lineStyle: { width: 1.5, color: t.accent },
        areaStyle: { color: t.accentFill, opacity: 1 },
        data: points.map((p) => [p.t, p.ok ? p.ms : null]),
      },
      {
        name: 'Failed check',
        type: 'line',
        showSymbol: true,
        symbol: 'circle',
        symbolSize: 7,
        color: t.status.down,
        itemStyle: { color: t.status.down },
        lineStyle: { width: 0, opacity: 0 },
        connectNulls: false,
        data: points.filter((p) => !p.ok).map((p) => [p.t, 0]),
        z: 10,
      },
    ],
  }), [points, t]);
  const valid = points.filter((p) => p.ok && p.ms != null).map((p) => p.ms as number);
  const avg = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  const label = `Latency over time, ${points.length} checks${avg != null ? `, average ${avg.toFixed(1)} ms` : ''}${failed ? `, ${failed} failed` : ''}`;
  return (
    <>
      <EChart option={option} height={220} ariaLabel={label} />
      <p className="mt-2 text-meta text-fg-3">
        {avg != null ? `Average ${avg.toFixed(1)} ms` : 'No successful check'}
        {failed > 0 && <span className="text-down"> · {failed} failed check{failed === 1 ? '' : 's'} (red dots)</span>}
        {threshold ? ` · degraded above ${threshold} ms` : ''} · gaps mean no data
      </p>
    </>
  );
}

/** Latency history with a range switch; failed checks marked, missing data left as gaps. */
export function LatencyCard({ hostId, threshold, className }: { hostId: number; threshold: number | null; className?: string }) {
  const [range, setRange] = useState<Range>('24');
  const history = useHostHistory(hostId, Number(range), 5000);
  return (
    <Card as="section" aria-labelledby="host-latency-title" className={className}>
      <CardHeader
        title="Latency"
        titleId="host-latency-title"
        actions={<SegmentedControl label="Latency range" size="sm" options={RANGES} value={range} onChange={setRange} />}
      />
      <QueryState
        query={history}
        compact
        loading={<Skeleton className="h-[220px] w-full" />}
        isEmpty={(d) => d.results.length === 0}
        empty={
          <EmptyState
            compact
            title="No checks in this period"
            description="Nodeglow has no check result for this host in the selected range."
          />
        }
      >
        {(d) => <LatencyChart results={d.results} threshold={threshold} />}
      </QueryState>
    </Card>
  );
}
