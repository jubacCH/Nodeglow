'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { EChart } from '@/components/charts/EChart';
import type { EChartsOption } from 'echarts';
import Link from 'next/link';

interface PiholeData {
  status: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  domains_blocked: number;
  dns_queries_all_types: number;
  clients: number;
  gravity_last_updated: string;
  api_version: number;
  top_queries: { domain: string; count: number }[];
  top_blocked: { domain: string; count: number }[];
  local_dns?: { domain: string; ip: string; type?: string }[];
  reply_types?: Record<string, number>;
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
          { value: data.blocked_today, name: 'Blocked', itemStyle: { color: '#ef4444' } },
          { value: data.queries_today - data.blocked_today, name: 'Allowed', itemStyle: { color: '#22c55e' } },
        ],
      },
    ],
  };

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Queries" value={formatNumber(data.queries_today)} />
        <StatCard label="Blocked" value={formatNumber(data.blocked_today)} />
        <StatCard label="Block Rate" value={`${data.blocked_pct.toFixed(1)}%`} />
        <StatCard label="Domains on List" value={formatNumber(data.domains_blocked)} />
      </div>

      {/* Status row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Status" value={data.status} />
        <StatCard label="Clients" value={data.clients} />
        <StatCard label="API Version" value={`v${data.api_version}`} />
        {data.gravity_last_updated && (
          <StatCard label="Gravity Updated" value={data.gravity_last_updated} />
        )}
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
            {(data.top_blocked ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.domain ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.domain}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!data.top_blocked || data.top_blocked.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>

        {/* Top queries */}
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Queries</h3>
          <div className="space-y-2">
            {(data.top_queries ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.domain ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.domain}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!data.top_queries || data.top_queries.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>
      </div>

      {/* Local DNS */}
      {data.local_dns && data.local_dns.length > 0 && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Local DNS Records ({data.local_dns.length})</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
            {data.local_dns.map((entry, i) => (
              <div key={i} className="flex items-center gap-2 text-xs py-1">
                {entry.type === 'CNAME' && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-sky-500/10 text-sky-400">CNAME</span>
                )}
                <span className="text-slate-300 font-mono truncate">{entry.domain}</span>
                <span className="text-slate-500">→</span>
                <Link href={'/hosts?q=' + encodeURIComponent(entry.ip)} className="text-sky-400 hover:underline font-mono truncate">
                  {entry.ip}
                </Link>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
