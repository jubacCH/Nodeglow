'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import { BigNumber } from '@/components/ui/BigNumber';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusPill } from '@/components/ui/StatusPill';
import { Pill, Tag } from '@/components/ui/Tag';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import { describeSyslog, fmtNum, formatClock, parseTime, syslogLevel, syslogRates } from '@/lib/dashboard';
import type { SectionError, SyslogSection } from '@/types/dashboard';
import { DashboardCard, KeyValues } from './DashboardCard';

/** Syslog rate against its learned band, errors in 24 h. */
export function SyslogCard({ syslog, loading, error, className }: {
  syslog: SyslogSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const t = useChartTheme();
  const level = syslog ? syslogLevel(syslog) : null;

  const option = useMemo((): EChartsOption | null => {
    if (!syslog) return null;
    const rates = syslogRates(syslog);
    const band = syslog.usual;
    return {
      animation: false,
      grid: { left: 2, right: 2, top: 6, bottom: 2, containLabel: true },
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const r = rates[(list[0] as { dataIndex?: number })?.dataIndex ?? 0];
          if (!r) return '';
          const usual = band ? `<br/>Usual ${fmtNum(band.low_per_min)}–${fmtNum(band.high_per_min)} /min` : '';
          return `<b>${formatClock(r.t)}</b><br/>Rate ${fmtNum(r.perMin)} /min<br/>Errors ${r.errors}${usual}`;
        },
      },
      xAxis: { type: 'time' as const, axisLabel: { hideOverlap: true, formatter: '{HH}:{mm}' } },
      yAxis: { type: 'value' as const, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      series: [
        {
          name: 'Rate',
          type: 'line' as const,
          data: rates.map((r) => [parseTime(r.t) ?? 0, r.perMin]),
          symbol: 'none',
          color: t.accent,
          lineStyle: { width: 1.6, color: t.accent },
          areaStyle: { color: t.accentFill, opacity: 1 },
          markArea: band
            ? { silent: true, itemStyle: { color: t.grid }, data: [[{ yAxis: band.low_per_min }, { yAxis: band.high_per_min }]] }
            : undefined,
        },
      ],
    };
  }, [syslog, t]);

  return (
    <DashboardCard id="dash-syslog" title="Syslog" loading={loading} error={error} className={className} actions={<span>24 h</span>}>
      {syslog && !syslog.receiving ? (
        <EmptyState
          compact
          title="No syslog received in 24 h"
          description="Point your devices at Nodeglow's syslog port to see rates and errors here."
          action={<Link href="/syslog" className="text-ui font-medium text-accent hover:text-accent-hover">Open logs</Link>}
        />
      ) : syslog ? (
        <>
          <div className="mb-2.5 flex flex-wrap items-end gap-3">
            <BigNumber value={fmtNum(syslog.current_per_min)} unit="/min" />
            {syslog.usual ? (
              level === 'above' ? (
                <StatusPill status="warning" className="mb-1.5">usual {fmtNum(syslog.usual.low_per_min)}–{fmtNum(syslog.usual.high_per_min)}</StatusPill>
              ) : (
                <Pill className="mb-1.5">usual {fmtNum(syslog.usual.low_per_min)}–{fmtNum(syslog.usual.high_per_min)}</Pill>
              )
            ) : (
              <Tag planned className="mb-1.5" title="Baselines are learned per hour and weekday">No baseline yet</Tag>
            )}
          </div>
          {option && <EChart option={option} height={116} ariaLabel={describeSyslog(syslog)} />}
          <KeyValues
            className="mt-3"
            rows={[
              ['Messages 24 h', syslog.total_24h.toLocaleString()],
              ['Error or worse', <span key="e" className={syslog.errors_24h > 0 ? 'text-fg' : undefined}>{syslog.errors_24h.toLocaleString()}</span>],
            ]}
          />
        </>
      ) : null}
    </DashboardCard>
  );
}
