'use client';

import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import { BigNumber } from '@/components/ui/BigNumber';
import { EmptyState } from '@/components/ui/EmptyState';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import { describeLatency, fmtMs, formatClock, parseTime } from '@/lib/dashboard';
import type { LatencySection, SectionError } from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';

/**
 * Q6 "what hangs together": latency of the hosts of the most severe open
 * incident, median and max per minute, with the incident onset marked.
 */
export function LatencyCard({ latency, loading, error, className }: {
  latency: LatencySection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const t = useChartTheme();
  const { open } = useDashboardCtx();

  const option = useMemo((): EChartsOption | null => {
    if (!latency) return null;
    const pts = latency.points.map((p) => ({ ...p, ts: parseTime(p.t) ?? 0 }));
    const onset = parseTime(latency.onset_at);
    const fmt = (v: number | null) => (v === null ? '—' : `${fmtMs(v)} ms`);
    return {
      animation: false,
      grid: { left: 4, right: 12, top: 24, bottom: 4, containLabel: true },
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
      yAxis: { type: 'value' as const, splitNumber: 4, axisLabel: { formatter: '{value} ms' } },
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

  const names = latency?.host_names.filter(Boolean) ?? [];
  const title = latency ? `Latency · hosts of #${latency.incident_id}` : 'Latency';

  return (
    <DashboardCard
      id="dash-latency"
      title={title}
      loading={loading}
      error={error}
      className={className}
      actions={latency && <span>Last {latency.window_hours} h</span>}
    >
      {!latency ? (
        <EmptyState
          compact
          title="No affected hosts to compare"
          description="Appears when an open incident has recorded hosts: their latency before and since its onset."
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-end gap-x-7 gap-y-2">
            <BigNumber
              size="sm"
              value={latency.median_since_ms !== null ? (latency.median_since_ms < 1 ? latency.median_since_ms.toFixed(2) : latency.median_since_ms.toFixed(1)) : null}
              unit="ms median"
              label={
                latency.median_before_ms !== null
                  ? `was ${fmtMs(latency.median_before_ms)} ms before ${formatClock(latency.onset_at)}`
                  : 'no checks before the onset in this window'
              }
            />
            <span className="flex items-center gap-1.5 text-meta text-fg-2">
              <i aria-hidden="true" className="inline-block h-[3px] w-3.5 rounded-[2px] bg-accent" />
              Median of {latency.host_count} host{latency.host_count === 1 ? '' : 's'}
            </span>
            <span className="flex items-center gap-1.5 text-meta text-fg-2">
              <i aria-hidden="true" className="inline-block h-2 w-3.5 rounded-[2px] bg-accent/20" />
              Max
            </span>
          </div>
          {latency.points.length === 0 ? (
            <EmptyState compact title="No checks in the last 2 h" description="These hosts were not checked in this window." />
          ) : (
            option && <EChart option={option} height={210} ariaLabel={describeLatency(latency)} />
          )}
          <p className="mt-2 truncate text-meta text-fg-3">
            <button type="button" onClick={() => open({ kind: 'incident', id: latency.incident_id })} className="text-accent hover:text-accent-hover">
              #{latency.incident_id} {latency.title}
            </button>
            {names.length > 0 && ` · ${names.slice(0, 4).join(', ')}${latency.host_count > 4 ? ` +${latency.host_count - 4}` : ''}`}
          </p>
        </>
      )}
    </DashboardCard>
  );
}
