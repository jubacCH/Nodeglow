'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { AnimatedCounter } from '@/components/data/AnimatedCounter';
import { HeatmapGrid } from '@/components/charts/HeatmapGrid';
import { EChart } from '@/components/charts/EChart';
import { useDashboard } from '@/hooks/queries/useDashboard';
import { formatLatency } from '@/lib/utils';
import {
  Server, ServerOff, Gauge, Wrench, ShieldAlert, Zap,
  ArrowUpDown, HardDrive, Activity,
} from 'lucide-react';
import Link from 'next/link';

export default function DashboardPage() {
  const { data, isLoading } = useDashboard();

  const avgLatency = data?.host_stats
    ? Math.round(
        data.host_stats
          .filter((h) => h.latency !== null && h.online)
          .reduce((sum, h) => sum + (h.latency ?? 0), 0) /
          (data.host_stats.filter((h) => h.latency !== null && h.online).length || 1),
      )
    : 0;

  const maintCount = data?.host_stats?.filter((h) => h.host.maintenance).length ?? 0;

  return (
    <div>
      <PageHeader title="Dashboard" description="Infrastructure overview" />

      {/* ── Quick Stats ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          icon={Server}
          label="Online"
          value={data?.online_count}
          color="text-emerald-400"
          loading={isLoading}
        />
        <StatCard
          icon={ServerOff}
          label="Offline"
          value={data?.offline_count}
          color="text-red-400"
          loading={isLoading}
        />
        <StatCard
          icon={Gauge}
          label="Avg Latency"
          value={avgLatency}
          suffix="ms"
          color="text-sky-400"
          loading={isLoading}
        />
        <StatCard
          icon={Wrench}
          label="Maintenance"
          value={maintCount}
          color="text-amber-400"
          loading={isLoading}
        />
      </div>

      {/* ── Main Grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        {/* Heatmap */}
        <GlassCard className="p-4 lg:col-span-2">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <Activity size={16} className="text-sky-400" /> 30-Day Availability
          </h3>
          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : data?.heatmap_data ? (
            <HeatmapGrid data={data.heatmap_data} days={data.heatmap_days} />
          ) : (
            <p className="text-sm text-slate-500">No data</p>
          )}
        </GlassCard>

        {/* Host List */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <Server size={16} className="text-sky-400" /> Hosts
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-1 max-h-[300px] overflow-y-auto">
              {data?.host_stats?.slice(0, 15).map((h) => (
                <Link
                  key={h.host.id}
                  href={`/hosts/${h.host.id}`}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={h.online === null ? 'unknown' : h.online ? 'online' : 'offline'} />
                  <span className="flex-1 text-sm text-slate-200 truncate">{h.host.name}</span>
                  <span className="text-xs font-mono text-slate-500">
                    {formatLatency(h.latency)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </GlassCard>

        {/* Integrations */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <Zap size={16} className="text-violet-400" /> Integrations
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-1 max-h-[300px] overflow-y-auto">
              {data?.integration_health?.map((int, i) => (
                <Link
                  key={i}
                  href={int.single_instance ? `/integration/${int.type}` : `/integration/${int.type}/${int.config_id}`}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={int.ok ? 'online' : 'offline'} />
                  <span className="flex-1 text-sm truncate" style={{ color: int.color }}>
                    {int.label}
                  </span>
                  <span className="text-xs text-slate-500 truncate max-w-[100px]">{int.name}</span>
                </Link>
              ))}
            </div>
          )}
        </GlassCard>

        {/* Syslog Rate */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <ArrowUpDown size={16} className="text-sky-400" /> Syslog Rate
          </h3>
          {isLoading || !data?.syslog_stats?.rate_data ? (
            <Skeleton className="h-[200px] w-full" />
          ) : (
            <EChart
              height={200}
              option={{
                tooltip: { trigger: 'axis' },
                legend: { show: false },
                xAxis: {
                  type: 'category',
                  data: data.syslog_stats.rate_data.labels,
                },
                yAxis: { type: 'value' },
                series: [
                  { name: 'Errors', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.errors, color: '#F87171' },
                  { name: 'Warnings', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.warnings, color: '#FBBF24' },
                  { name: 'Info', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.info, color: '#38BDF8' },
                ],
              }}
            />
          )}
        </GlassCard>

        {/* Active Alerts */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <ShieldAlert size={16} className="text-red-400" /> Recent Incidents
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : data?.recent_incidents?.length ? (
            <div className="space-y-1.5 max-h-[250px] overflow-y-auto">
              {data.recent_incidents.slice(0, 8).map((inc) => (
                <Link
                  key={inc.id}
                  href={`/incidents/${inc.id}`}
                  className="flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <Badge variant="severity" severity={inc.severity as 'critical' | 'warning' | 'info'}>
                    {inc.severity}
                  </Badge>
                  <span className="flex-1 text-sm text-slate-200 truncate">{inc.title}</span>
                  <Badge>{inc.status}</Badge>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
              All clear — no active incidents
            </p>
          )}
        </GlassCard>

        {/* Storage Pools */}
        {data?.storage_pools?.length ? (
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
              <HardDrive size={16} className="text-sky-400" /> Storage
            </h3>
            <div className="space-y-3 max-h-[250px] overflow-y-auto">
              {data.storage_pools.map((pool, i) => (
                <div key={i}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-slate-300">{pool.label} — {pool.pool_name}</span>
                    <span className="text-xs font-mono text-slate-400">
                      {pool.used_human} / {pool.total_human}
                    </span>
                  </div>
                  <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(pool.used_pct, 100)}%`,
                        background: pool.used_pct >= 90
                          ? '#F87171'
                          : pool.used_pct >= 75
                            ? '#FBBF24'
                            : '#34D399',
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>
        ) : null}

        {/* Speedtest */}
        {data?.speedtest_data && (
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
              <Gauge size={16} className="text-blue-400" /> Speedtest
            </h3>
            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <p className="text-2xl font-bold text-sky-400">{Math.round(data.speedtest_data.download_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Down Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-violet-400">{Math.round(data.speedtest_data.upload_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Up Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-emerald-400">{Math.round(data.speedtest_data.ping_ms)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Ping ms</p>
              </div>
            </div>
            <p className="text-[10px] text-slate-600 text-center mt-2">{data.speedtest_data.isp}</p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  loading,
  suffix,
}: {
  icon: React.ElementType;
  label: string;
  value?: number;
  color: string;
  loading: boolean;
  suffix?: string;
}) {
  return (
    <GlassCard className="p-4 flex items-center gap-4">
      <div className={`p-2 rounded-lg bg-white/[0.04] ${color}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
        {loading ? (
          <Skeleton className="h-7 w-16 mt-1" />
        ) : (
          <p className={`text-2xl font-bold ${color}`}>
            <AnimatedCounter value={value ?? 0} suffix={suffix} />
          </p>
        )}
      </div>
    </GlassCard>
  );
}
