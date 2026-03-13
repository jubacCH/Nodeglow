'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { EChart } from '@/components/charts/EChart';
import type { EChartsOption } from 'echarts';

interface PiholeStats {
  total_queries: number;
  blocked: number;
  block_pct: number;
  domains_on_list: number;
}

interface PiholeEntry {
  domain?: string;
  client?: string;
  count: number;
}

interface PiholeData {
  stats: PiholeStats;
  top_blocked: PiholeEntry[];
  top_clients: PiholeEntry[];
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function PiholeDetail({ data }: { data: PiholeData }) {
  const { stats, top_blocked, top_clients } = data;

  const pieOption: EChartsOption = {
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: 'transparent', borderWidth: 2 },
        label: { show: false },
        data: [
          { value: stats.blocked, name: 'Blocked', itemStyle: { color: '#ef4444' } },
          { value: stats.total_queries - stats.blocked, name: 'Allowed', itemStyle: { color: '#22c55e' } },
        ],
      },
    ],
  };

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Queries" value={formatNumber(stats.total_queries)} />
        <StatCard label="Blocked" value={formatNumber(stats.blocked)} />
        <StatCard label="Block Rate" value={`${stats.block_pct.toFixed(1)}%`} />
        <StatCard label="Domains on List" value={formatNumber(stats.domains_on_list)} />
      </div>

      {/* Pie chart + lists */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pie chart */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Blocked vs Allowed</h3>
          <EChart option={pieOption} height={220} />
        </GlassCard>

        {/* Top blocked */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Blocked Domains</h3>
          <div className="space-y-2">
            {(top_blocked ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.domain ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.domain}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!top_blocked || top_blocked.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>

        {/* Top clients */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Clients</h3>
          <div className="space-y-2">
            {(top_clients ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.client ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.client}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!top_clients || top_clients.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
