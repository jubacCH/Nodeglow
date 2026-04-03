'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { get } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Skeleton } from '@/components/ui/Skeleton';
import { ArrowDownToLine, ArrowUpFromLine, Network, Trophy } from 'lucide-react';
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type EChartsOption = any;

const EChart = dynamic(
  () => import('@/components/charts/EChart').then((m) => ({ default: m.EChart })),
  { ssr: false, loading: () => <Skeleton className="h-[300px] w-full" /> },
);

// --- Types ---

interface BandwidthSummary {
  total_rx_bps: number;
  total_tx_bps: number;
  top_talkers: TopTalker[];
  by_source: SourceSummary[];
}

interface TopTalker {
  source_type: string;
  source_id: string;
  interface_name: string;
  rx_rate_bps: number;
  tx_rate_bps: number;
}

interface SourceSummary {
  source_type: string;
  total_rx_bps: number;
  total_tx_bps: number;
}

interface BandwidthInterface {
  source_type: string;
  source_id: string;
  interface_name: string;
  rx_rate_bps: number;
  tx_rate_bps: number;
  last_seen: string;
}

interface HistoryPoint {
  timestamp: string;
  rx_rate_bps: number;
  tx_rate_bps: number;
}

// --- Helpers ---

function formatBps(bps: number): string {
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} Kbps`;
  return `${Math.round(bps)} bps`;
}

function rateColor(bps: number): string {
  if (bps >= 1e9) return 'text-red-400';
  if (bps >= 1e8) return 'text-amber-400';
  return 'text-emerald-400';
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h ago`;
  return `${Math.floor(diff / 86400_000)}d ago`;
}

const HOUR_OPTIONS = [
  { label: '1h', value: 1 },
  { label: '6h', value: 6 },
  { label: '24h', value: 24 },
  { label: '7d', value: 168 },
] as const;

// --- Component ---

export default function BandwidthPage() {
  useEffect(() => { document.title = 'Bandwidth | Nodeglow'; }, []);

  const [hours, setHours] = useState<number>(24);

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['bandwidth-summary'],
    queryFn: () => get<BandwidthSummary>('/api/bandwidth'),
    refetchInterval: 15_000,
  });

  const { data: interfaces, isLoading: ifLoading } = useQuery({
    queryKey: ['bandwidth-interfaces'],
    queryFn: () => get<BandwidthInterface[]>('/api/bandwidth/interfaces'),
    refetchInterval: 15_000,
  });

  const { data: history } = useQuery({
    queryKey: ['bandwidth-history', hours],
    queryFn: () => get<HistoryPoint[]>(`/api/bandwidth/history?hours=${hours}`),
    refetchInterval: 15_000,
  });

  const sortedInterfaces = useMemo(() => {
    if (!interfaces) return [];
    return [...interfaces].sort(
      (a, b) => (b.rx_rate_bps + b.tx_rate_bps) - (a.rx_rate_bps + a.tx_rate_bps),
    );
  }, [interfaces]);

  const topTalker = summary?.top_talkers?.[0];

  // --- Chart options ---

  const trafficOption = useMemo((): EChartsOption => {
    if (!history || history.length === 0) return {};
    const timestamps = history.map((p) => p.timestamp);
    const rxData = history.map((p) => +(p.rx_rate_bps / 1e6).toFixed(2));
    const txData = history.map((p) => +(p.tx_rate_bps / 1e6).toFixed(2));

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as { seriesName: string; value: number; axisValueLabel: string; color: string }[];
          if (!Array.isArray(items) || items.length === 0) return '';
          const time = new Date(items[0].axisValueLabel).toLocaleString();
          const lines = items.map(
            (i) => `<span style="color:${i.color}">\u25CF</span> ${i.seriesName}: <b>${i.value.toFixed(2)} Mbps</b>`,
          );
          return `${time}<br/>${lines.join('<br/>')}`;
        },
      },
      legend: { data: ['Download', 'Upload'] },
      grid: { left: 50, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: timestamps,
        axisLabel: {
          formatter: (v: string) => {
            const d = new Date(v);
            return hours <= 6
              ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
              : d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
          },
        },
      },
      yAxis: {
        type: 'value',
        name: 'Mbps',
        axisLabel: { formatter: '{value}' },
      },
      series: [
        {
          name: 'Download',
          type: 'line',
          data: rxData,
          smooth: true,
          showSymbol: false,
          areaStyle: { opacity: 0.15 },
          itemStyle: { color: '#3b82f6' },
        },
        {
          name: 'Upload',
          type: 'line',
          data: txData,
          smooth: true,
          showSymbol: false,
          areaStyle: { opacity: 0.15 },
          itemStyle: { color: '#22c55e' },
        },
      ],
    };
  }, [history, hours]);

  const topTalkersOption = useMemo((): EChartsOption => {
    const talkers = summary?.top_talkers?.slice(0, 10) ?? [];
    if (talkers.length === 0) return {};

    const names = talkers.map((t) => `${t.source_type}/${t.interface_name}`);
    const rxData = talkers.map((t) => +(t.rx_rate_bps / 1e6).toFixed(2));
    const txData = talkers.map((t) => +(t.tx_rate_bps / 1e6).toFixed(2));

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as { seriesName: string; value: number; name: string; color: string }[];
          if (!Array.isArray(items) || items.length === 0) return '';
          const lines = items.map(
            (i) => `<span style="color:${i.color}">\u25CF</span> ${i.seriesName}: <b>${i.value.toFixed(2)} Mbps</b>`,
          );
          return `${items[0].name}<br/>${lines.join('<br/>')}`;
        },
      },
      legend: { data: ['Download', 'Upload'] },
      grid: { left: 140, right: 20, top: 30, bottom: 20 },
      xAxis: {
        type: 'value',
        name: 'Mbps',
        axisLabel: { formatter: '{value}' },
      },
      yAxis: {
        type: 'category',
        data: names,
        inverse: true,
        axisLabel: { width: 120, overflow: 'truncate' },
      },
      series: [
        {
          name: 'Download',
          type: 'bar',
          stack: 'total',
          data: rxData,
          itemStyle: { color: '#3b82f6' },
        },
        {
          name: 'Upload',
          type: 'bar',
          stack: 'total',
          data: txData,
          itemStyle: { color: '#22c55e' },
        },
      ],
    };
  }, [summary]);

  return (
    <div>
      <PageHeader
        title="Bandwidth"
        description="Network traffic monitoring"
      />

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-500/10">
              <ArrowDownToLine size={20} className="text-blue-400" />
            </div>
            <div>
              {summaryLoading ? (
                <Skeleton className="h-7 w-24" />
              ) : (
                <p className="text-xl font-semibold text-slate-100">
                  {formatBps(summary?.total_rx_bps ?? 0)}
                </p>
              )}
              <p className="text-xs text-slate-400 mt-0.5">Total Download</p>
            </div>
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-green-500/10">
              <ArrowUpFromLine size={20} className="text-green-400" />
            </div>
            <div>
              {summaryLoading ? (
                <Skeleton className="h-7 w-24" />
              ) : (
                <p className="text-xl font-semibold text-slate-100">
                  {formatBps(summary?.total_tx_bps ?? 0)}
                </p>
              )}
              <p className="text-xs text-slate-400 mt-0.5">Total Upload</p>
            </div>
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-violet-500/10">
              <Network size={20} className="text-violet-400" />
            </div>
            <div>
              {ifLoading ? (
                <Skeleton className="h-7 w-12" />
              ) : (
                <p className="text-xl font-semibold text-slate-100">
                  {interfaces?.length ?? 0}
                </p>
              )}
              <p className="text-xs text-slate-400 mt-0.5">Active Interfaces</p>
            </div>
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-500/10">
              <Trophy size={20} className="text-amber-400" />
            </div>
            <div>
              {summaryLoading ? (
                <Skeleton className="h-7 w-32" />
              ) : topTalker ? (
                <>
                  <p className="text-xl font-semibold text-slate-100">
                    {formatBps(topTalker.rx_rate_bps + topTalker.tx_rate_bps)}
                  </p>
                  <p className="text-xs text-slate-400 mt-0.5 truncate max-w-[160px]">
                    {topTalker.source_type}/{topTalker.interface_name}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-xl font-semibold text-slate-500">--</p>
                  <p className="text-xs text-slate-400 mt-0.5">Top Talker</p>
                </>
              )}
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Traffic chart */}
      <GlassCard className="p-4 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-200">Traffic Overview</h3>
          <div className="flex gap-1">
            {HOUR_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setHours(opt.value)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  hours === opt.value
                    ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                    : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        {history && history.length > 0 ? (
          <EChart option={trafficOption} height={320} />
        ) : (
          <div className="flex items-center justify-center h-[320px] text-sm text-slate-500">
            No traffic data available
          </div>
        )}
      </GlassCard>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Interface table */}
        <GlassCard>
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-semibold text-slate-200">Interfaces</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Source</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Interface</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Download</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Upload</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {ifLoading &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i} className="border-b border-white/[0.06]">
                      <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                    </tr>
                  ))}
                {sortedInterfaces.map((iface, idx) => {
                  const isActive = Date.now() - new Date(iface.last_seen).getTime() < 300_000;
                  return (
                    <tr key={idx} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <StatusDot status={isActive ? 'online' : 'offline'} />
                          <span className="text-slate-300 text-xs">{iface.source_type}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-300">
                        {iface.interface_name}
                      </td>
                      <td className={`px-4 py-3 text-right font-mono text-xs ${rateColor(iface.rx_rate_bps)}`}>
                        {formatBps(iface.rx_rate_bps)}
                      </td>
                      <td className={`px-4 py-3 text-right font-mono text-xs ${rateColor(iface.tx_rate_bps)}`}>
                        {formatBps(iface.tx_rate_bps)}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-slate-500">
                        {timeAgo(iface.last_seen)}
                      </td>
                    </tr>
                  );
                })}
                {!ifLoading && sortedInterfaces.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-sm text-slate-500">
                      No interfaces reporting
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </GlassCard>

        {/* Top talkers bar chart */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-semibold text-slate-200 mb-4">Top Talkers</h3>
          {summary?.top_talkers && summary.top_talkers.length > 0 ? (
            <EChart option={topTalkersOption} height={360} />
          ) : (
            <div className="flex items-center justify-center h-[360px] text-sm text-slate-500">
              No data available
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
