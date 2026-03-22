'use client';

import { useEffect, useState, useMemo } from 'react';
import Link from 'next/link';
import { MessageSquare, BarChart3, Brain, AlertTriangle, Server, Activity, ShieldAlert } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { EChart } from '@/components/charts/EChart';
import { useSyslogStats } from '@/hooks/queries/useSyslogStats';
import type { EChartsOption } from 'echarts';

const SEVERITY_LABELS: Record<number, string> = {
  0: 'Emergency',
  1: 'Alert',
  2: 'Critical',
  3: 'Error',
  4: 'Warning',
  5: 'Notice',
  6: 'Info',
  7: 'Debug',
};

const SEVERITY_CHART_COLORS: Record<number, string> = {
  0: '#ef4444',
  1: '#f87171',
  2: '#fb923c',
  3: '#f97316',
  4: '#fbbf24',
  5: '#60a5fa',
  6: '#38bdf8',
  7: '#64748b',
};

const TIME_RANGES = [
  { label: '1h', hours: 1 },
  { label: '6h', hours: 6 },
  { label: '12h', hours: 12 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 168 },
];

export default function SyslogDashboardPage() {
  useEffect(() => { document.title = 'Syslog Dashboard | Nodeglow'; }, []);
  const [hours, setHours] = useState(24);
  const { data, isLoading } = useSyslogStats(hours);

  const errorCount = useMemo(() => {
    if (!data) return 0;
    return data.severity_distribution
      .filter((s) => s.severity <= 3)
      .reduce((sum, s) => sum + s.count, 0);
  }, [data]);

  const uniqueHosts = data?.top_hosts.length ?? 0;

  const topSeverity = useMemo(() => {
    if (!data?.severity_distribution.length) return null;
    const sorted = [...data.severity_distribution].sort((a, b) => a.severity - b.severity);
    return sorted[0];
  }, [data]);

  // Severity pie chart
  const severityOption = useMemo((): EChartsOption => {
    if (!data) return {};
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { show: false },
      series: [{
        type: 'pie',
        radius: ['45%', '75%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: 'transparent', borderWidth: 2 },
        label: { show: true, formatter: '{b}', fontSize: 11 },
        data: data.severity_distribution.map((s) => ({
          name: SEVERITY_LABELS[s.severity] ?? `Sev ${s.severity}`,
          value: s.count,
          itemStyle: { color: SEVERITY_CHART_COLORS[s.severity] ?? '#64748b' },
        })),
      }],
    };
  }, [data]);

  // Message rate area chart
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
      legend: { data: ['Total', 'Errors'], textStyle: { fontSize: 11 } },
      grid: { left: 40, right: 16, top: 36, bottom: 24 },
      xAxis: { type: 'category', data: buckets, axisLabel: { fontSize: 10, rotate: hours > 24 ? 30 : 0 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      series: [
        {
          name: 'Total',
          type: 'line',
          smooth: true,
          areaStyle: { opacity: 0.15 },
          itemStyle: { color: '#38bdf8' },
          data: data.message_rate.map((r) => r.count),
        },
        {
          name: 'Errors',
          type: 'line',
          smooth: true,
          areaStyle: { opacity: 0.15 },
          itemStyle: { color: '#f87171' },
          data: data.message_rate.map((r) => r.errors),
        },
      ],
    };
  }, [data, hours]);

  // Top hosts horizontal bar chart
  const hostsOption = useMemo((): EChartsOption => {
    if (!data?.top_hosts.length) return {};
    const hosts = [...data.top_hosts].reverse();
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 120, right: 24, top: 8, bottom: 8 },
      xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      yAxis: {
        type: 'category',
        data: hosts.map((h) => h.hostname),
        axisLabel: { fontSize: 10, width: 110, overflow: 'truncate' },
      },
      series: [{
        type: 'bar',
        data: hosts.map((h) => h.count),
        itemStyle: { color: '#38bdf8', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 20,
      }],
    };
  }, [data]);

  // Top apps horizontal bar chart
  const appsOption = useMemo((): EChartsOption => {
    if (!data?.top_apps.length) return {};
    const apps = [...data.top_apps].reverse();
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 120, right: 24, top: 8, bottom: 8 },
      xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      yAxis: {
        type: 'category',
        data: apps.map((a) => a.app_name),
        axisLabel: { fontSize: 10, width: 110, overflow: 'truncate' },
      },
      series: [{
        type: 'bar',
        data: apps.map((a) => a.count),
        itemStyle: { color: '#a78bfa', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 20,
      }],
    };
  }, [data]);

  return (
    <div>
      <PageHeader
        title="Syslog"
        description="Dashboard - aggregated syslog statistics and trends"
      />

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4">
        <Link href="/syslog" className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-white/[0.03] transition-colors">
          <MessageSquare size={15} /> Messages
        </Link>
        <span className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-white/[0.06] text-slate-100">
          <BarChart3 size={15} /> Dashboard
        </span>
        <Link href="/syslog/templates" className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-white/[0.03] transition-colors">
          <Brain size={15} /> Intelligence
        </Link>
      </div>

      {/* Time range selector */}
      <div className="flex flex-wrap gap-2 mb-6">
        {TIME_RANGES.map((r) => (
          <button
            key={r.hours}
            onClick={() => setHours(r.hours)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              hours === r.hours
                ? 'bg-sky-500/30 text-sky-300 border border-sky-500/50'
                : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className="rounded-md bg-sky-500/20 p-2">
            <Activity className="h-4 w-4 text-sky-400" />
          </div>
          <div>
            <p className="text-xs text-slate-500">Total Messages</p>
            {isLoading ? (
              <Skeleton className="h-5 w-16 mt-0.5" />
            ) : (
              <p className="text-lg font-semibold text-slate-100">{(data?.total ?? 0).toLocaleString()}</p>
            )}
          </div>
        </GlassCard>

        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className="rounded-md bg-red-500/20 p-2">
            <AlertTriangle className="h-4 w-4 text-red-400" />
          </div>
          <div>
            <p className="text-xs text-slate-500">Errors (Sev 0-3)</p>
            {isLoading ? (
              <Skeleton className="h-5 w-16 mt-0.5" />
            ) : (
              <p className="text-lg font-semibold text-red-400">{errorCount.toLocaleString()}</p>
            )}
          </div>
        </GlassCard>

        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className="rounded-md bg-emerald-500/20 p-2">
            <Server className="h-4 w-4 text-emerald-400" />
          </div>
          <div>
            <p className="text-xs text-slate-500">Unique Hosts</p>
            {isLoading ? (
              <Skeleton className="h-5 w-12 mt-0.5" />
            ) : (
              <p className="text-lg font-semibold text-slate-100">{uniqueHosts}</p>
            )}
          </div>
        </GlassCard>

        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className="rounded-md bg-amber-500/20 p-2">
            <ShieldAlert className="h-4 w-4 text-amber-400" />
          </div>
          <div>
            <p className="text-xs text-slate-500">Top Severity</p>
            {isLoading ? (
              <Skeleton className="h-5 w-20 mt-0.5" />
            ) : (
              <p className="text-lg font-semibold text-slate-100">
                {topSeverity ? topSeverity.label : 'None'}
              </p>
            )}
          </div>
        </GlassCard>
      </div>

      {/* Charts grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Severity Distribution</h3>
          {isLoading ? (
            <Skeleton className="h-[260px] w-full" />
          ) : data?.severity_distribution.length ? (
            <EChart option={severityOption} height={260} />
          ) : (
            <div className="h-[260px] flex items-center justify-center text-sm text-slate-500">No data</div>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Message Rate</h3>
          {isLoading ? (
            <Skeleton className="h-[260px] w-full" />
          ) : data?.message_rate.length ? (
            <EChart option={rateOption} height={260} />
          ) : (
            <div className="h-[260px] flex items-center justify-center text-sm text-slate-500">No data</div>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top 10 Hosts</h3>
          {isLoading ? (
            <Skeleton className="h-[300px] w-full" />
          ) : data?.top_hosts.length ? (
            <EChart option={hostsOption} height={300} />
          ) : (
            <div className="h-[300px] flex items-center justify-center text-sm text-slate-500">No data</div>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top 10 Applications</h3>
          {isLoading ? (
            <Skeleton className="h-[300px] w-full" />
          ) : data?.top_apps.length ? (
            <EChart option={appsOption} height={300} />
          ) : (
            <div className="h-[300px] flex items-center justify-center text-sm text-slate-500">No data</div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
