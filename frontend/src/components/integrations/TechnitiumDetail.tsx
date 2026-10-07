'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { EChart } from '@/components/charts/EChart';
import type { EChartsOption } from 'echarts';

interface ClusterNode {
  name: string;
  type: string;
  state: string;
  version: string;
  ip: string;
  last_seen?: string | null;
}

interface TechnitiumData {
  status: string;
  version: string;
  server_domain: string;
  update_available: boolean;
  update_version: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  server_failures_today: number;
  nxdomain_today: number;
  clients: number;
  domains_blocked: number;
  zones: number;
  blocklist_next_update: string;
  top_queries: { domain: string; count: number }[];
  top_blocked: { domain: string; count: number }[];
  top_clients: { client: string; count: number }[];
  cluster_initialized: boolean;
  cluster_domain: string;
  cluster_nodes: ClusterNode[];
  cluster_nodes_unhealthy: number;
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

function TopList({ title, rows }: { title: string; rows: { label: string; count: number }[] }) {
  return (
    <GlassCard className="p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-3">{title}</h3>
      <div className="space-y-2">
        {rows.slice(0, 10).map((row, i) => (
          <div key={row.label || i} className="flex items-center justify-between text-xs">
            <span className="text-slate-300 truncate mr-2 font-mono">{row.label}</span>
            <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(row.count)}</span>
          </div>
        ))}
        {rows.length === 0 && <p className="text-xs text-slate-500">No data</p>}
      </div>
    </GlassCard>
  );
}

export function TechnitiumDetail({ data }: { data: TechnitiumData }) {
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
      {/* Stat cards (last 24h) */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Queries (24h)" value={formatNumber(data.queries_today)} />
        <StatCard label="Blocked (24h)" value={formatNumber(data.blocked_today)} />
        <StatCard label="Block Rate" value={`${(data.blocked_pct ?? 0).toFixed(1)}%`} />
        <StatCard label="Domains on List" value={formatNumber(data.domains_blocked)} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Blocking" value={data.status} />
        <StatCard label="Clients (24h)" value={data.clients} />
        <StatCard label="Server Failures (24h)" value={formatNumber(data.server_failures_today ?? 0)} />
        <StatCard
          label={data.update_available ? `Update to ${data.update_version} available` : 'Version (up to date)'}
          value={`v${data.version}`}
        />
      </div>

      {/* Cluster */}
      {data.cluster_initialized && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            Cluster {data.cluster_domain}
            {data.cluster_nodes_unhealthy > 0 && (
              <span className="ml-2 text-red-400">{data.cluster_nodes_unhealthy} node(s) unreachable</span>
            )}
          </h3>
          <div className="space-y-2">
            {(data.cluster_nodes ?? []).map((node) => {
              const healthy = node.state === 'Self' || node.state === 'Connected';
              return (
                <div key={node.name} className="flex items-center gap-3 text-xs">
                  <StatusDot status={healthy ? 'online' : 'offline'} pulse={!healthy} />
                  <span className="text-slate-300 font-mono truncate">{node.name}</span>
                  <span className="text-slate-500">{node.ip}</span>
                  <span className="text-slate-400">{node.type}</span>
                  <span className={healthy ? 'text-slate-500' : 'text-red-400'}>{node.state}</span>
                  <span className="text-slate-500 ml-auto tabular-nums">v{node.version}</span>
                </div>
              );
            })}
          </div>
        </GlassCard>
      )}

      {/* Pie chart + lists */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Blocked vs Allowed</h3>
          <EChart option={pieOption} height={220} />
        </GlassCard>
        <TopList title="Top Blocked Domains" rows={(data.top_blocked ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top Queries" rows={(data.top_queries ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top Clients" rows={(data.top_clients ?? []).map((e) => ({ label: e.client, count: e.count }))} />
      </div>
    </div>
  );
}
