'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { EChart } from '@/components/charts/EChart';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { useEffect, useMemo } from 'react';
import type { Digest } from '@/types';
import type { EChartsOption } from 'echarts';
import { AlertTriangle, Server, FileText, Plug, HardDrive, ShieldAlert, Clock } from 'lucide-react';
import Link from 'next/link';

export default function DigestPage() {
  useEffect(() => { document.title = 'Digest | Nodeglow'; }, []);
  const { data, isLoading } = useQuery({
    queryKey: ['digest'],
    queryFn: () => get<Digest>('/api/v1/digest'),
  });

  return (
    <div>
      <PageHeader
        title="Weekly Digest"
        description={data ? `${data.period_start.split('T')[0]} — ${data.period_end.split('T')[0]}` : 'Loading...'}
      />

      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <SummaryCard
          icon={AlertTriangle}
          label="Incidents"
          value={data?.incidents.total}
          color="text-red-400"
          loading={isLoading}
        />
        <SummaryCard
          icon={Server}
          label="Avg Uptime"
          value={data?.hosts.avg_uptime != null ? `${data.hosts.avg_uptime.toFixed(1)}%` : undefined}
          color={
            (data?.hosts.avg_uptime ?? 100) >= 99.9
              ? 'text-emerald-400'
              : (data?.hosts.avg_uptime ?? 100) >= 95
                ? 'text-amber-400'
                : 'text-red-400'
          }
          loading={isLoading}
        />
        <SummaryCard
          icon={FileText}
          label="Syslog Messages"
          value={data?.syslog.total != null ? data.syslog.total.toLocaleString() : undefined}
          color="text-sky-400"
          loading={isLoading}
        />
        <SummaryCard
          icon={Clock}
          label="MTTR"
          value={data?.incidents.mttr_minutes != null ? `${Math.round(data.incidents.mttr_minutes)}min` : '—'}
          color="text-violet-400"
          loading={isLoading}
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Incidents by Severity</h3>
          {isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <SeverityChart data={data?.incidents.by_severity} />
          )}
        </GlassCard>
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Integration Success Rates</h3>
          {isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <IntegrationChart integrations={data?.integrations} />
          )}
        </GlassCard>
      </div>

      {/* Top Incidents + Worst Hosts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Incidents</h3>
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : data?.incidents.top?.length ? (
            <div className="space-y-2">
              {data.incidents.top.map((inc) => (
                <Link
                  key={inc.id}
                  href={`/incidents/${inc.id}`}
                  className="flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <Badge variant="severity" severity={inc.severity as 'critical' | 'warning' | 'info'}>
                    {inc.severity}
                  </Badge>
                  <span className="text-sm text-slate-200 truncate">{inc.title}</span>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-emerald-400 text-center py-4">No incidents this week</p>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Worst Performers</h3>
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : data?.hosts.worst?.length ? (
            <div className="space-y-2">
              {data.hosts.worst.map((h) => (
                <Link
                  key={h.id}
                  href={`/hosts/${h.id}`}
                  className="flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <span className="text-sm text-slate-200 flex-1 truncate">{h.name}</span>
                  <span className={`text-xs font-mono ${h.uptime >= 99.9 ? 'text-emerald-400' : h.uptime >= 95 ? 'text-amber-400' : 'text-red-400'}`}>
                    {h.uptime.toFixed(1)}%
                  </span>
                  <span className="text-[10px] text-slate-500">{h.failures} failures</span>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-emerald-400 text-center py-4">All hosts performing well</p>
          )}
        </GlassCard>
      </div>

      {/* Integrations + Syslog Top Errors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <Plug size={14} />
            Integration Health
          </h3>
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : data?.integrations?.length ? (
            <div className="space-y-2">
              {data.integrations.map((intg) => (
                <div key={`${intg.type}-${intg.name}`} className="flex items-center gap-3 px-2 py-2">
                  <span className="text-sm text-slate-200 flex-1 truncate">{intg.name}</span>
                  <span className="text-[10px] text-slate-500 uppercase">{intg.type}</span>
                  {intg.success_rate != null ? (
                    <span className={`text-xs font-mono ${intg.success_rate >= 99 ? 'text-emerald-400' : intg.success_rate >= 90 ? 'text-amber-400' : 'text-red-400'}`}>
                      {intg.success_rate.toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-xs text-slate-500">—</span>
                  )}
                  {intg.failures > 0 && (
                    <span className="text-[10px] text-red-400">{intg.failures} fail</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500 text-center py-4">No integration data</p>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <FileText size={14} />
            Top Syslog Patterns
          </h3>
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : data?.syslog.top_errors?.length ? (
            <div className="space-y-2">
              {data.syslog.top_errors.map((err, i) => (
                <div key={i} className="flex items-start gap-3 px-2 py-2">
                  <span className="text-xs font-mono text-sky-400 shrink-0 mt-0.5">
                    {err.count.toLocaleString()}x
                  </span>
                  <span className="text-xs text-slate-300 font-mono break-all leading-relaxed">
                    {err.template}
                  </span>
                  {err.noise_score != null && err.noise_score > 0.7 && (
                    <span className="shrink-0 text-[10px] text-slate-500 border border-white/10 rounded px-1">noise</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500 text-center py-4">No syslog patterns</p>
          )}
        </GlassCard>
      </div>

      {/* SSL + Storage Warnings */}
      {((data?.ssl_expiring?.length ?? 0) > 0 || (data?.storage_predictions?.length ?? 0) > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {(data?.ssl_expiring?.length ?? 0) > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
                <ShieldAlert size={14} className="text-amber-400" />
                SSL Certificates Expiring Soon
              </h3>
              <div className="space-y-2">
                {data!.ssl_expiring.map((ssl, i) => (
                  <div key={i} className="flex items-center gap-3 px-2 py-2">
                    <span className="text-sm text-slate-200 flex-1 truncate">{ssl.name}</span>
                    <span className="text-xs text-slate-400 font-mono">{ssl.hostname}</span>
                    <span className={`text-xs font-mono font-bold ${ssl.days <= 7 ? 'text-red-400' : ssl.days <= 14 ? 'text-amber-400' : 'text-sky-400'}`}>
                      {ssl.days}d
                    </span>
                  </div>
                ))}
              </div>
            </GlassCard>
          )}

          {(data?.storage_predictions?.length ?? 0) > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
                <HardDrive size={14} className="text-amber-400" />
                Storage Predictions
              </h3>
              <div className="space-y-2">
                {data!.storage_predictions.map((pred, i) => (
                  <div key={i} className="flex items-center gap-3 px-2 py-2">
                    <span className="text-sm text-slate-200 flex-1 truncate">{pred.host}</span>
                    <span className="text-xs text-slate-400 font-mono">{pred.disk}</span>
                    {pred.current_usage_pct != null && (
                      <span className="text-xs text-slate-500">{pred.current_usage_pct.toFixed(0)}% used</span>
                    )}
                    <span className={`text-xs font-mono font-bold ${(pred.days_until_full ?? 999) <= 14 ? 'text-red-400' : (pred.days_until_full ?? 999) <= 30 ? 'text-amber-400' : 'text-sky-400'}`}>
                      {pred.days_until_full != null ? `${pred.days_until_full}d` : '—'}
                    </span>
                  </div>
                ))}
              </div>
            </GlassCard>
          )}
        </div>
      )}
    </div>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  color,
  loading,
}: {
  icon: React.ElementType;
  label: string;
  value?: number | string;
  color: string;
  loading: boolean;
}) {
  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-2 mb-1">
        <Icon size={14} className="text-slate-400" />
        <span className="text-xs text-slate-500 uppercase tracking-wider">{label}</span>
      </div>
      {loading ? (
        <Skeleton className="h-8 w-16 mt-1" />
      ) : (
        <p className={`text-2xl font-bold ${color}`}>{value ?? '—'}</p>
      )}
    </GlassCard>
  );
}

function SeverityChart({ data }: { data?: Record<string, number> }) {
  const option = useMemo((): EChartsOption => {
    if (!data || Object.keys(data).length === 0) {
      return { graphic: { type: 'text', left: 'center', top: 'center', style: { text: 'No incidents', fontSize: 13, fill: '#64748B' } } };
    }
    const colorMap: Record<string, string> = {
      critical: '#EF4444', warning: '#FBBF24', info: '#38BDF8',
    };
    const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: true,
        label: { show: true, formatter: '{b}\n{c}', fontSize: 11 },
        data: entries.map(([name, value]) => ({
          name, value, itemStyle: { color: colorMap[name] || '#818CF8' },
        })),
      }],
    };
  }, [data]);

  return <EChart option={option} height={200} />;
}

function IntegrationChart({ integrations }: { integrations?: Digest['integrations'] }) {
  const option = useMemo((): EChartsOption => {
    if (!integrations?.length) {
      return { graphic: { type: 'text', left: 'center', top: 'center', style: { text: 'No data', fontSize: 13, fill: '#64748B' } } };
    }
    const items = integrations
      .filter((i) => i.success_rate != null)
      .sort((a, b) => (a.success_rate ?? 100) - (b.success_rate ?? 100))
      .slice(0, 12);
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 16, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
      yAxis: { type: 'category', data: items.map((i) => i.name), axisLabel: { width: 80, overflow: 'truncate' } },
      series: [{
        type: 'bar',
        data: items.map((i) => ({
          value: i.success_rate,
          itemStyle: {
            color: (i.success_rate ?? 100) >= 99 ? '#10B981' : (i.success_rate ?? 100) >= 90 ? '#FBBF24' : '#EF4444',
          },
        })),
      }],
    };
  }, [integrations]);

  return <EChart option={option} height={200} />;
}
