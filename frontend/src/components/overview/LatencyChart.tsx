'use client';

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import { describeLatency, fmtMs, formatClock, parseTime } from '@/lib/dashboard';
import type { LatencySection } from '@/types/dashboard';

/**
 * Latency of an incident's hosts: median and max per minute, the onset
 * marked. Shown inside the incident it belongs to (concept F), not as a card.
 */
export function LatencyChart({ latency, height = 170 }: { latency: LatencySection; height?: number }) {
  const t = useChartTheme();

  const option = useMemo((): EChartsOption => {
    const pts = latency.points.map((p) => ({ ...p, ts: parseTime(p.t) ?? 0 }));
    const onset = parseTime(latency.onset_at);
    const fmt = (v: number | null) => (v === null ? '—' : `${fmtMs(v)} ms`);
    return {
      animation: false,
      grid: { left: 4, right: 12, top: 18, bottom: 4, containLabel: true },
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const idx = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const p = pts[idx];
          if (!p) return '';
          const total = p.ok + p.failed;
          return `<b>${formatClock(p.t)}</b><br/>Median ${fmt(p.median_ms)}<br/>Max ${fmt(p.max_ms)}<br/>Answering ${p.ok} of ${total} checks`;
        },
      },
      xAxis: { type: 'time' as const, axisLabel: { hideOverlap: true, formatter: '{HH}:{mm}' } },
      yAxis: { type: 'value' as const, splitNumber: 3, axisLabel: { formatter: '{value} ms' } },
      series: [
        {
          name: 'Max',
          type: 'line' as const,
          data: pts.map((p) => [p.ts, p.max_ms]),
          symbol: 'none',
          lineStyle: { width: 0 },
          areaStyle: { color: t.accentFill, opacity: 1 },
          color: t.accentFill,
          z: 1,
        },
        {
          name: 'Median',
          type: 'line' as const,
          data: pts.map((p) => [p.ts, p.median_ms]),
          symbol: 'none',
          color: t.accent,
          lineStyle: { width: 1.8, color: t.accent },
          areaStyle: { opacity: 0 },
          z: 2,
          markLine: onset
            ? {
                symbol: 'none',
                silent: true,
                lineStyle: { color: t.status.down, type: 'dashed' as const, width: 1 },
                label: { formatter: `Onset ${formatClock(latency.onset_at)}`, color: t.text2, position: 'end' as const, fontSize: 11 },
                data: [{ xAxis: onset }],
              }
            : undefined,
        },
      ],
    };
  }, [latency, t]);

  if (latency.points.length === 0) {
    return <p className="text-meta text-fg-3">These hosts were not checked in the last {latency.window_hours} h.</p>;
  }
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-fg-2">
        <span>
          Latency of {latency.host_count} host{latency.host_count === 1 ? '' : 's'}, last {latency.window_hours} h
          {latency.median_before_ms !== null && latency.median_since_ms !== null && (
            <> · median <b className="num font-medium text-fg">{fmtMs(latency.median_since_ms)} ms</b>, was {fmtMs(latency.median_before_ms)} ms</>
          )}
        </span>
        <span className="flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-[3px] w-3.5 rounded-pill bg-accent" />Median</span>
        <span className="flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-2 w-3.5 rounded-chip bg-accent/20" />Max</span>
      </div>
      <EChart option={option} height={height} ariaLabel={describeLatency(latency)} />
    </div>
  );
}
