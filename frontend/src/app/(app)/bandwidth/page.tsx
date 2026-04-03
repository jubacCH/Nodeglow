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

const EChart = dynamic(
  () => import('@/components/charts/EChart').then((m) => ({ default: m.EChart })),
  { ssr: false, loading: () => <div className="h-[300px] bg-white/5 rounded animate-pulse" /> },
);

/* ---------- helpers ---------- */

function formatBps(bps: number | undefined | null): string {
  if (!bps || bps <= 0) return '0 bps';
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gbps`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} Kbps`;
  return `${Math.round(bps)} bps`;
}

function rateColor(bps: number | undefined | null): string {
  if (!bps) return 'text-slate-400';
  if (bps >= 1e9) return 'text-red-400';
  if (bps >= 1e8) return 'text-amber-400';
  return 'text-emerald-400';
}

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  if (isNaN(diff)) return '—';
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

/* ---------- types ---------- */

interface BandwidthSummary {
  total_rx_bps?: number;
  total_tx_bps?: number;
  total_interfaces?: number;
  top_talkers?: Array<{
    source_type?: string;
    source_id?: string;
    source_name?: string;
    interface_name?: string;
    rx_rate_bps?: number;
    tx_rate_bps?: number;
  }>;
  by_source?: Array<{
    source_type?: string;
    total_rx_bps?: number;
    total_tx_bps?: number;
  }>;
}

interface BandwidthInterface {
  source_type?: string;
  source_id?: string;
  interface_name?: string;
  display_name?: string;
  rx_rate_bps?: number;
  tx_rate_bps?: number;
  last_seen?: string;
}

interface HistoryPoint {
  timestamp?: string;
  rx_rate_bps?: number;
  tx_rate_bps?: number;
}

/* ---------- component ---------- */

export default function BandwidthPage() {
  useEffect(() => { document.title = 'Bandwidth | Nodeglow'; }, []);

  const [hours, setHours] = useState(24);

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
    if (!interfaces || !Array.isArray(interfaces)) return [];
    return [...interfaces].sort(
      (a, b) => ((b.rx_rate_bps ?? 0) + (b.tx_rate_bps ?? 0)) - ((a.rx_rate_bps ?? 0) + (a.tx_rate_bps ?? 0)),
    );
  }, [interfaces]);

  const topTalker = summary?.top_talkers?.[0];

  /* --- chart options --- */

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const trafficOption = useMemo((): any => {
    if (!history || !Array.isArray(history) || history.length === 0) return {};
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['Download', 'Upload'] },
      grid: { left: 50, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: history.map((p) => p.timestamp ?? ''),
        axisLabel: {
          formatter: (v: string) => {
            try {
              const d = new Date(v);
              return hours <= 6
                ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
            } catch { return v; }
          },
        },
      },
      yAxis: { type: 'value', name: 'Mbps' },
      series: [
        {
          name: 'Download', type: 'line', smooth: true, showSymbol: false,
          areaStyle: { opacity: 0.15 }, itemStyle: { color: '#3b82f6' },
          data: history.map((p) => +((p.rx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
        {
          name: 'Upload', type: 'line', smooth: true, showSymbol: false,
          areaStyle: { opacity: 0.15 }, itemStyle: { color: '#22c55e' },
          data: history.map((p) => +((p.tx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
      ],
    };
  }, [history, hours]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const topTalkersOption = useMemo((): any => {
    const talkers = summary?.top_talkers?.slice(0, 10) ?? [];
    if (talkers.length === 0) return {};
    const labels = talkers.map((t) => {
      let name = t.interface_name ?? '';
      // Clean up device/ prefix and MAC addresses
      name = name.replace(/^device\//, '');
      // If it's a MAC-like string, prefer source_name
      if (/^[0-9A-Fa-f]{12}/.test(name) && t.source_name) {
        name = t.source_name;
      }
      // Truncate long names
      if (name.length > 25) name = name.slice(0, 22) + '...';
      return name;
    });

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const items = params as Array<{ seriesName: string; value: number; name: string; color: string }>;
          if (!Array.isArray(items) || !items.length) return '';
          const lines = items.map(i => `<span style="color:${i.color}">\u25CF</span> ${i.seriesName}: <b>${i.value.toFixed(1)} Mbps</b>`);
          return `${items[0].name}<br/>${lines.join('<br/>')}`;
        },
      },
      legend: { data: ['Download', 'Upload'], top: 0, right: 0 },
      grid: { left: 10, right: 30, top: 30, bottom: 10, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => v >= 1000 ? `${(v/1000).toFixed(1)}G` : `${v}M` },
      },
      yAxis: {
        type: 'category', inverse: true,
        data: labels,
        axisLabel: { width: 160, overflow: 'break', fontSize: 11 },
      },
      series: [
        {
          name: 'Download', type: 'bar', stack: 'total', itemStyle: { color: '#3b82f6' },
          data: talkers.map((t) => +((t.rx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
        {
          name: 'Upload', type: 'bar', stack: 'total', itemStyle: { color: '#22c55e' },
          data: talkers.map((t) => +((t.tx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
      ],
    };
  }, [summary]);

  return (
    <div>
      <PageHeader title="Bandwidth" description="Network traffic monitoring" />

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-500/10">
              <ArrowDownToLine size={20} className="text-blue-400" />
            </div>
            <div>
              {summaryLoading ? <Skeleton className="h-7 w-24" /> : (
                <p className="text-xl font-semibold text-slate-100">{formatBps(summary?.total_rx_bps)}</p>
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
              {summaryLoading ? <Skeleton className="h-7 w-24" /> : (
                <p className="text-xl font-semibold text-slate-100">{formatBps(summary?.total_tx_bps)}</p>
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
              {summaryLoading ? <Skeleton className="h-7 w-16" /> : (
                <p className="text-xl font-semibold text-slate-100">{summary?.total_interfaces ?? sortedInterfaces.length}</p>
              )}
              <p className="text-xs text-slate-400 mt-0.5">Interfaces</p>
            </div>
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-500/10">
              <Trophy size={20} className="text-amber-400" />
            </div>
            <div>
              {summaryLoading ? <Skeleton className="h-7 w-28" /> : (
                <>
                  <p className="text-xl font-semibold text-slate-100">
                    {topTalker ? formatBps((topTalker.rx_rate_bps ?? 0) + (topTalker.tx_rate_bps ?? 0)) : '—'}
                  </p>
                  <p className="text-xs text-slate-400 mt-0.5 truncate max-w-[160px]">
                    {topTalker ? (topTalker.source_name ? `${topTalker.source_name} / ${topTalker.interface_name}` : topTalker.interface_name) : 'Top Talker'}
                  </p>
                </>
              )}
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Traffic chart */}
      <GlassCard className="p-4 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-200">Traffic</h3>
          <div className="flex gap-1">
            {HOUR_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setHours(opt.value)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  hours === opt.value
                    ? 'bg-sky-500/20 text-sky-300'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        {history && Array.isArray(history) && history.length > 0 ? (
          <EChart option={trafficOption} height={300} />
        ) : (
          <div className="flex items-center justify-center h-[300px] text-sm text-slate-500">
            No traffic data yet — data will appear after agent snapshots are collected
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
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Interface</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Download</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Upload</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {ifLoading && Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                  </tr>
                ))}
                {sortedInterfaces.map((iface, idx) => {
                  const isActive = iface.last_seen
                    ? (Date.now() - new Date(iface.last_seen).getTime()) < 300_000
                    : false;
                  return (
                    <tr key={idx} className="border-b border-white/[0.06] hover:bg-white/[0.02]">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <StatusDot status={isActive ? 'online' : 'offline'} />
                          <span className="text-slate-300 text-xs">{iface.display_name ?? `${iface.source_type}/${iface.interface_name}`}</span>
                        </div>
                      </td>
                      <td className={`px-4 py-3 text-right font-mono text-xs ${rateColor(iface.rx_rate_bps)}`}>
                        {formatBps(iface.rx_rate_bps)}
                      </td>
                      <td className={`px-4 py-3 text-right font-mono text-xs ${rateColor(iface.tx_rate_bps)}`}>
                        {formatBps(iface.tx_rate_bps)}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-slate-500">{timeAgo(iface.last_seen)}</td>
                    </tr>
                  );
                })}
                {!ifLoading && sortedInterfaces.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                      No interfaces reporting yet
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </GlassCard>

        {/* Top talkers */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-semibold text-slate-200 mb-4">Top Talkers</h3>
          {summary?.top_talkers && summary.top_talkers.length > 0 ? (
            <EChart option={topTalkersOption} height={400} />
          ) : (
            <div className="flex items-center justify-center h-[400px] text-sm text-slate-500">
              No data available
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
