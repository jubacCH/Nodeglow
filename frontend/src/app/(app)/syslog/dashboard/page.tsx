'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { BarChart3 } from 'lucide-react';
import type { EChartsOption } from 'echarts';
import { PageHeader } from '@/components/layout/PageHeader';
import { EChart } from '@/components/charts/LazyEChart';
import { SEVERITY_LABELS, SeverityBadge, severityChartColor } from '@/components/syslog/severity';
import { BigNumber } from '@/components/ui/BigNumber';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { useSyslogStats } from '@/hooks/queries/useSyslogStats';
import { useChartTheme, type ChartTokens } from '@/lib/chart-theme';

const TIME_RANGES = [
  { label: '1h', hours: 1, long: 'last hour' },
  { label: '6h', hours: 6, long: 'last 6 hours' },
  { label: '12h', hours: 12, long: 'last 12 hours' },
  { label: '24h', hours: 24, long: 'last 24 hours' },
  { label: '7d', hours: 168, long: 'last 7 days' },
];

/** The stats endpoint returns at most this many hosts/apps. */
const TOP_N = 10;

function barOption(t: ChartTokens, labels: string[], values: number[], color: string): EChartsOption {
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      inverse: true,
      data: labels,
      axisLabel: { width: 120, overflow: 'truncate', fontFamily: t.monoFamily },
    },
    series: [{
      type: 'bar',
      name: 'Messages',
      data: values,
      itemStyle: { color, borderRadius: [0, 3, 3, 0] },
      barMaxWidth: 18,
    }],
  };
}

function ChartCard({ title, meta, children }: { title: string; meta?: ReactNode; children: ReactNode }) {
  return (
    <Card as="section" aria-label={title}>
      <CardHeader title={title} meta={meta} />
      {children}
    </Card>
  );
}

export default function SyslogDashboardPage() {
  useEffect(() => { document.title = 'Log overview | Nodeglow'; }, []);
  const [hours, setHours] = useState(24);
  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useSyslogStats(hours);
  const t = useChartTheme();
  const range = TIME_RANGES.find((r) => r.hours === hours) ?? TIME_RANGES[3];

  const errorCount = useMemo(() => {
    if (!data) return null;
    return data.severity_distribution
      .filter((s) => s.severity <= 3)
      .reduce((sum, s) => sum + s.count, 0);
  }, [data]);

  const topSeverity = useMemo(() => {
    if (!data?.severity_distribution.length) return null;
    return [...data.severity_distribution].sort((a, b) => a.severity - b.severity)[0];
  }, [data]);

  // Severity distribution: one bar per level, error = down, warning = warning, rest grey.
  const severityOption = useMemo((): EChartsOption => {
    if (!data) return {};
    const rows = [...data.severity_distribution].sort((a, b) => a.severity - b.severity);
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: 'value' },
      yAxis: {
        type: 'category',
        inverse: true,
        data: rows.map((s) => SEVERITY_LABELS[s.severity] ?? `Sev ${s.severity}`),
      },
      series: [{
        type: 'bar',
        name: 'Messages',
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        data: rows.map((s) => ({ value: s.count, itemStyle: { color: severityChartColor(s.severity, t) } })),
      }],
    };
  }, [data, t]);

  const rateOption = useMemo((): EChartsOption => {
    if (!data?.message_rate.length) return {};
    const buckets = data.message_rate.map((r) => {
      const d = new Date(r.bucket);
      return hours <= 24
        ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    });
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['Total', 'Error or worse'], top: 0, right: 0 },
      grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: 'category', data: buckets, boundaryGap: false },
      yAxis: { type: 'value' },
      series: [
        {
          name: 'Total',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 2, color: t.accent },
          itemStyle: { color: t.accent },
          areaStyle: { color: t.accentFill, opacity: 1 },
          data: data.message_rate.map((r) => r.count),
        },
        {
          name: 'Error or worse',
          type: 'line',
          showSymbol: false,
          lineStyle: { width: 2, color: t.status.down },
          itemStyle: { color: t.status.down },
          data: data.message_rate.map((r) => r.errors),
        },
      ],
    };
  }, [data, hours, t]);

  const hostsOption = useMemo(
    () => (data?.top_hosts.length ? barOption(t, data.top_hosts.map((h) => h.hostname || h.source_ip), data.top_hosts.map((h) => h.count), t.series[0]) : {}),
    [data, t],
  );
  const appsOption = useMemo(
    () => (data?.top_apps.length ? barOption(t, data.top_apps.map((a) => a.app_name || '(none)'), data.top_apps.map((a) => a.count), t.series[1]) : {}),
    [data, t],
  );

  const asOf = formatAsOf(dataUpdatedAt);
  const hostCount = data?.top_hosts.length ?? null;
  const noMessages = data !== undefined && data.total === 0;

  const chartBody = (has: boolean | undefined, height: number, chart: ReactNode, emptyTitle: string) => {
    if (isLoading) return <Skeleton className={height > 260 ? 'h-[300px] w-full' : 'h-[260px] w-full'} />;
    if (!data) return null;
    if (!has) {
      return (
        <div className={`grid place-items-center ${height > 260 ? 'min-h-[300px]' : 'min-h-[260px]'}`}>
          <EmptyState compact variant="no-results" icon={BarChart3} title={emptyTitle} description={`Nothing in the ${range.long}.`} />
        </div>
      );
    }
    return chart;
  };

  return (
    <div>
      <PageHeader
        title="Log overview"
        description={
          <>
            Aggregated syslog statistics for the {range.long}
            {asOf && <> · Updated {asOf}</>}
          </>
        }
        actions={
          <SegmentedControl
            label="Time range"
            value={String(hours)}
            onChange={(v) => setHours(Number(v))}
            options={TIME_RANGES.map((r) => ({ value: String(r.hours), label: r.label, ariaLabel: r.long }))}
          />
        }
      />

      {isError && data && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}

      {isError && !data ? (
        <Card>
          <QueryErrorState error={error} onRetry={refetch} title="Could not load log statistics" />
        </Card>
      ) : (
        <>
          {/* Key figures */}
          <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Card>
              {isLoading ? <Skeleton className="h-[52px] w-28" /> : (
                <BigNumber size="sm" value={data ? data.total.toLocaleString() : null} label="Messages" />
              )}
            </Card>
            <Card>
              {isLoading ? <Skeleton className="h-[52px] w-24" /> : (
                <BigNumber
                  size="sm"
                  value={errorCount === null ? null : errorCount.toLocaleString()}
                  state={errorCount ? 'down' : undefined}
                  label="Error or worse (sev 0–3)"
                />
              )}
            </Card>
            <Card>
              {isLoading ? <Skeleton className="h-[52px] w-16" /> : (
                <BigNumber
                  size="sm"
                  value={hostCount === null ? null : hostCount >= TOP_N ? `${TOP_N}+` : hostCount}
                  label="Sending hosts"
                />
              )}
            </Card>
            <Card>
              {isLoading ? <Skeleton className="h-[52px] w-24" /> : (
                <div className="min-w-0">
                  <div className="flex h-[30px] items-center">
                    {topSeverity ? (
                      <SeverityBadge severity={topSeverity.severity} className="h-[24px] px-2 text-meta" />
                    ) : (
                      <span className="num font-display text-num-sm font-medium text-fg-3">—</span>
                    )}
                  </div>
                  <div className="mt-1 text-meta text-fg-2">Highest severity seen</div>
                </div>
              )}
            </Card>
          </div>

          {noMessages && (
            <Card className="mb-4">
              <EmptyState
                variant="not-configured"
                title={`No syslog messages in the ${range.long}`}
                description={<>Pick a longer time range, or check that devices send syslog to Nodeglow. <Link href="/syslog" className="text-accent hover:text-accent-hover">Open Logs</Link></>}
              />
            </Card>
          )}

          {!noMessages && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ChartCard title="Message rate" meta={range.label}>
                {chartBody(
                  Boolean(data?.message_rate.length),
                  260,
                  <EChart
                    option={rateOption}
                    height={260}
                    ariaLabel={`Message rate over the ${range.long}: total messages and messages with severity error or worse.`}
                  />,
                  'No message rate data',
                )}
              </ChartCard>

              <ChartCard title="Severity distribution" meta={range.label}>
                {chartBody(
                  Boolean(data?.severity_distribution.length),
                  260,
                  <EChart
                    option={severityOption}
                    height={260}
                    ariaLabel={`Messages per severity: ${(data?.severity_distribution ?? [])
                      .map((s) => `${SEVERITY_LABELS[s.severity] ?? s.severity} ${s.count}`)
                      .join(', ')}.`}
                  />,
                  'No severity data',
                )}
              </ChartCard>

              <ChartCard title="Top hosts" meta={`by messages · top ${TOP_N}`}>
                {chartBody(
                  Boolean(data?.top_hosts.length),
                  300,
                  <EChart
                    option={hostsOption}
                    height={300}
                    ariaLabel={`Top hosts by message count: ${(data?.top_hosts ?? []).map((h) => `${h.hostname} ${h.count}`).join(', ')}.`}
                  />,
                  'No host data',
                )}
              </ChartCard>

              <ChartCard title="Top applications" meta={`by messages · top ${TOP_N}`}>
                {chartBody(
                  Boolean(data?.top_apps.length),
                  300,
                  <EChart
                    option={appsOption}
                    height={300}
                    ariaLabel={`Top applications by message count: ${(data?.top_apps ?? []).map((a) => `${a.app_name} ${a.count}`).join(', ')}.`}
                  />,
                  'No application data',
                )}
              </ChartCard>
            </div>
          )}
        </>
      )}
    </div>
  );
}
