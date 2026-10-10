'use client';

import { useEffect, useMemo, type ReactNode } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import type { EChartsOption } from 'echarts';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { BigNumber } from '@/components/ui/BigNumber';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tag } from '@/components/ui/Tag';
import { EChart } from '@/components/charts/LazyEChart';
import { get } from '@/lib/api';
import { useChartTheme } from '@/lib/chart-theme';
import { STATE_TEXT, type HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import type { Digest } from '@/types';

/* ---------- Thresholds → states ---------- */

function uptimeState(pct: number | null | undefined): HealthState | undefined {
  if (pct == null) return undefined;
  if (pct >= 99.9) return 'ok';
  if (pct >= 95) return 'degraded';
  return 'down';
}

function successState(pct: number | null | undefined): HealthState {
  if (pct == null) return 'unknown';
  if (pct >= 99) return 'ok';
  if (pct >= 90) return 'degraded';
  return 'down';
}

function daysState(days: number | null | undefined, crit: number, warn: number): HealthState | undefined {
  if (days == null) return undefined;
  if (days <= crit) return 'down';
  if (days <= warn) return 'warning';
  return undefined;
}

const day = (iso: string) => iso.split('T')[0];

/* ---------- Page ---------- */

export default function DigestPage() {
  useEffect(() => { document.title = 'Weekly report | Nodeglow'; }, []);
  const query = useQuery({
    queryKey: ['digest'],
    queryFn: () => get<Digest>('/api/v1/digest'),
  });
  const data = query.data;

  return (
    <div>
      <PageHeader
        title="Weekly report"
        description={data ? `${day(data.period_start)} – ${day(data.period_end)} · last 7 days` : 'Incidents, availability, logs and expiring resources of the last 7 days'}
      />

      <QueryState query={query} loading={<DigestSkeleton />} errorTitle="Could not load the weekly report">
        {(d) => <DigestContent data={d} />}
      </QueryState>
    </div>
  );
}

function DigestSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[104px] rounded-card" />)}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-[260px] rounded-card" />
        <Skeleton className="h-[260px] rounded-card" />
      </div>
    </div>
  );
}

function Section({ title, meta, children, className }: { title: string; meta?: ReactNode; children: ReactNode; className?: string }) {
  const id = `h-${title.toLowerCase().replace(/[^a-z]+/g, '-')}`;
  return (
    <Card as="section" aria-labelledby={id} className={className}>
      <CardHeader title={title} titleId={id} meta={meta} />
      {children}
    </Card>
  );
}

function DigestContent({ data }: { data: Digest }) {
  const periodEnd = new Date(data.period_end).toLocaleDateString();
  const uptime = data.hosts.avg_uptime;
  const ssl = data.ssl_expiring ?? [];
  const storage = data.storage_predictions ?? [];

  return (
    <div className="space-y-4">
      {/* Key figures */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <BigNumber value={data.incidents.total} label="Incidents" />
        </Card>
        <Card>
          <BigNumber
            value={uptime != null && data.hosts.total > 0 ? uptime.toFixed(1) : null}
            unit="%"
            state={data.hosts.total > 0 ? uptimeState(uptime) : undefined}
            label={`Average uptime · ${data.hosts.total} hosts`}
          />
        </Card>
        <Card>
          <BigNumber value={data.syslog.total != null ? data.syslog.total.toLocaleString() : null} label={`Syslog messages · ${(data.syslog.errors ?? 0).toLocaleString()} errors`} />
        </Card>
        <Card>
          <BigNumber value={data.incidents.mttr_minutes != null ? Math.round(data.incidents.mttr_minutes) : null} unit="min" label="Mean time to resolve" />
        </Card>
      </div>

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Incidents by severity">
          <SeverityChart data={data.incidents.by_severity} asOf={periodEnd} />
        </Section>
        <Section title="Integration success rate" meta="Lowest 12">
          <IntegrationChart integrations={data.integrations} />
        </Section>
      </div>

      {/* Top incidents + worst hosts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Top incidents">
          {data.incidents.top?.length ? (
            <ul className="-mx-2">
              {data.incidents.top.map((inc) => (
                <li key={inc.id}>
                  <Link href={`/incidents/${inc.id}`} className="flex min-w-0 items-center gap-3 rounded-ng-sm px-2 py-2 hover:bg-surface-2">
                    <Badge variant="severity" severity={inc.severity as 'critical' | 'warning' | 'info'}>{inc.severity}</Badge>
                    <span className="min-w-0 flex-1 truncate text-ui text-fg">{inc.title}</span>
                    <span className="shrink-0 font-mono text-meta text-fg-3">#{inc.id}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState compact variant="confirmed" title="No incidents in this period" asOf={periodEnd} />
          )}
        </Section>

        <Section title="Lowest uptime">
          {data.hosts.worst?.length ? (
            <ul className="-mx-2">
              {data.hosts.worst.map((h) => (
                <li key={h.id}>
                  <Link prefetch={false} href={`/hosts/${h.id}`} className="flex min-w-0 items-center gap-3 rounded-ng-sm px-2 py-2 hover:bg-surface-2">
                    <span className="min-w-0 flex-1 truncate text-ui text-fg">{h.name}</span>
                    <span className="num shrink-0 text-meta text-fg-3">{h.failures} failures</span>
                    <span className={cn('num w-14 shrink-0 text-right text-ui font-medium', STATE_TEXT[uptimeState(h.uptime) ?? 'unknown'])}>
                      {h.uptime.toFixed(1)}%
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : data.hosts.total > 0 ? (
            <EmptyState compact variant="confirmed" title="All hosts met their uptime" asOf={periodEnd} />
          ) : (
            <EmptyState compact title="No hosts monitored" description="Add hosts to see availability in this report." />
          )}
        </Section>
      </div>

      {/* Integrations + syslog patterns */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Integration health">
          {data.integrations?.length ? (
            <ul className="divide-y divide-border">
              {data.integrations.map((intg) => {
                const st = successState(intg.success_rate);
                return (
                  <li key={`${intg.type}-${intg.name}`} className="flex min-w-0 items-center gap-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-ui text-fg">{intg.name}</span>
                    <Badge>{intg.type}</Badge>
                    {intg.failures > 0 && <span className="num shrink-0 text-meta text-down">{intg.failures} failed</span>}
                    <span className={cn('num w-14 shrink-0 text-right text-ui', STATE_TEXT[st])}>
                      {intg.success_rate != null ? `${intg.success_rate.toFixed(1)}%` : '—'}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState compact title="No integration data" description="No integration was polled in this period." />
          )}
        </Section>

        <Section title="Top syslog patterns">
          {data.syslog.top_errors?.length ? (
            <ul className="divide-y divide-border">
              {data.syslog.top_errors.map((err, i) => (
                <li key={i} className="flex min-w-0 items-start gap-3 py-2">
                  <span className="num w-16 shrink-0 text-right text-meta font-medium text-fg">{err.count.toLocaleString()}×</span>
                  <span className="min-w-0 flex-1 break-all font-mono text-meta leading-relaxed text-fg-2">{err.template}</span>
                  {err.noise_score != null && err.noise_score > 0.7 && <Tag className="shrink-0">noise</Tag>}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState compact title="No syslog patterns" description="No error patterns were learned in this period." />
          )}
        </Section>
      </div>

      {/* Expiring resources */}
      {(ssl.length > 0 || storage.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {ssl.length > 0 && (
            <Section title="Certificates expiring soon">
              <ul className="divide-y divide-border">
                {ssl.map((c, i) => {
                  const st = daysState(c.days, 7, 14);
                  return (
                    <li key={i} className="flex min-w-0 items-center gap-3 py-2">
                      <span className="min-w-0 flex-1 truncate text-ui text-fg">{c.name}</span>
                      <span className="min-w-0 truncate font-mono text-meta text-fg-2">{c.hostname}</span>
                      <span className={cn('num w-12 shrink-0 text-right text-ui font-medium', st ? STATE_TEXT[st] : 'text-fg')}>{c.days} d</span>
                    </li>
                  );
                })}
              </ul>
            </Section>
          )}
          {storage.length > 0 && (
            <Section title="Storage forecast" meta="Linear projection">
              <ul className="divide-y divide-border">
                {storage.map((p, i) => {
                  const st = daysState(p.days_until_full, 14, 30);
                  return (
                    <li key={i} className="flex min-w-0 items-center gap-3 py-2">
                      <span className="min-w-0 flex-1 truncate text-ui text-fg">{p.host}</span>
                      <span className="min-w-0 truncate font-mono text-meta text-fg-2">{p.disk}</span>
                      {p.current_usage_pct != null && <span className="num shrink-0 text-meta text-fg-3">{p.current_usage_pct.toFixed(0)}% used</span>}
                      <span className={cn('num w-14 shrink-0 text-right text-ui font-medium', st ? STATE_TEXT[st] : 'text-fg-2')}>
                        {p.days_until_full != null ? `${p.days_until_full} d` : '—'}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- Charts (colours from the theme tokens) ---------- */

function SeverityChart({ data, asOf }: { data?: Record<string, number>; asOf: string }) {
  const t = useChartTheme();
  const entries = useMemo(() => Object.entries(data ?? {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]), [data]);
  const option = useMemo((): EChartsOption => {
    const color = (name: string) =>
      name === 'critical' ? t.status.down : name === 'warning' ? t.status.warning : name === 'info' ? t.series[1] : t.series[3];
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie',
        radius: ['45%', '72%'],
        avoidLabelOverlap: true,
        label: { show: true, formatter: '{b}\n{c}', fontSize: 11, color: t.text2 },
        data: entries.map(([name, value]) => ({ name, value, itemStyle: { color: color(name) } })),
      }],
    };
  }, [entries, t]);

  if (entries.length === 0) {
    return <EmptyState compact variant="confirmed" title="No incidents in this period" asOf={asOf} />;
  }
  return (
    <EChart
      option={option}
      height={220}
      ariaLabel={`Incidents by severity: ${entries.map(([k, v]) => `${v} ${k}`).join(', ')}`}
    />
  );
}

function IntegrationChart({ integrations }: { integrations?: Digest['integrations'] }) {
  const t = useChartTheme();
  const items = useMemo(
    () => (integrations ?? [])
      .filter((i) => i.success_rate != null)
      .sort((a, b) => (a.success_rate ?? 100) - (b.success_rate ?? 100))
      .slice(0, 12),
    [integrations],
  );
  const option = useMemo((): EChartsOption => ({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => `${Number(v).toFixed(1)}%` },
    grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    yAxis: { type: 'category', data: items.map((i) => i.name), axisLabel: { width: 96, overflow: 'truncate' } },
    series: [{
      type: 'bar',
      data: items.map((i) => {
        const st = successState(i.success_rate);
        return { value: i.success_rate, itemStyle: { color: st === 'ok' ? t.status.ok : st === 'degraded' ? t.status.degraded : t.status.down } };
      }),
    }],
  }), [items, t]);

  if (items.length === 0) {
    return <EmptyState compact title="No integration data" description="No integration was polled in this period." />;
  }
  return (
    <EChart
      option={option}
      height={Math.max(160, items.length * 26 + 24)}
      ariaLabel={`Integration success rate, lowest first: ${items.map((i) => `${i.name} ${i.success_rate?.toFixed(1)}%`).join(', ')}`}
    />
  );
}
