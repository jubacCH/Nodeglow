'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button } from '@/components/ui/Button';
import { EChart } from '@/components/charts/EChart';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { formatUptime } from '@/lib/utils';
import { ArrowLeft, Monitor, Cpu, HardDrive, MemoryStick } from 'lucide-react';
import Link from 'next/link';
import type { Agent, AgentSnapshot } from '@/types';

interface AgentDetail extends Agent {
  snapshots: AgentSnapshot[];
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const agentId = Number(id);

  const { data, isLoading } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => get<AgentDetail>(`/api/v1/agents/${agentId}`),
    enabled: agentId > 0,
    refetchInterval: 15_000,
  });

  const online = data?.last_seen
    ? Date.now() - new Date(data.last_seen).getTime() < 120_000
    : false;

  const latest = data?.snapshots?.[0];

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Agents', href: '/agents' }, { label: data?.name ?? `Agent #${agentId}` }]} />
      <PageHeader
        title={data?.name ?? 'Agent'}
        description={data?.hostname ?? ''}
        actions={
          <Link href="/agents">
            <Button variant="ghost" size="sm"><ArrowLeft size={16} /> Back</Button>
          </Link>
        }
      />

      {/* Status Header */}
      <GlassCard className="p-4 mb-6">
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : data ? (
          <div className="flex items-center gap-6 flex-wrap">
            <div className="flex items-center gap-3">
              <StatusDot status={online ? 'online' : 'offline'} pulse={!online} />
              <span className="text-sm text-slate-300">{online ? 'Online' : 'Offline'}</span>
            </div>
            {data.platform && <Badge>{data.platform}</Badge>}
            {data.arch && <Badge>{data.arch}</Badge>}
            {data.agent_version && <Badge>v{data.agent_version}</Badge>}
            {data.last_seen && (
              <span className="text-xs text-slate-500">
                Last seen: {new Date(data.last_seen).toLocaleString()}
              </span>
            )}
            {latest?.uptime_s != null && (
              <span className="text-xs text-slate-500">
                Uptime: {formatUptime(latest.uptime_s)}
              </span>
            )}
          </div>
        ) : null}
      </GlassCard>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <MetricCard
          icon={Cpu}
          label="CPU"
          value={latest?.cpu_pct}
          suffix="%"
          loading={isLoading}
        />
        <MetricCard
          icon={MemoryStick}
          label="Memory"
          value={latest?.mem_pct}
          suffix="%"
          extra={latest ? `${((latest.mem_used_mb ?? 0) / 1024).toFixed(1)} / ${((latest.mem_total_mb ?? 0) / 1024).toFixed(1)} GB` : undefined}
          loading={isLoading}
        />
        <MetricCard
          icon={HardDrive}
          label="Disk"
          value={latest?.disk_pct}
          suffix="%"
          loading={isLoading}
        />
      </div>

      {/* CPU/Memory Chart */}
      <GlassCard className="p-4 mb-6">
        <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
          <Monitor size={16} className="text-sky-400" /> Performance History
        </h3>
        {isLoading || !data?.snapshots?.length ? (
          <Skeleton className="h-[250px] w-full" />
        ) : (
          <EChart
            height={250}
            option={{
              tooltip: { trigger: 'axis' },
              legend: { data: ['CPU', 'Memory', 'Disk'] },
              xAxis: {
                type: 'category',
                data: data.snapshots.map((s) =>
                  new Date(s.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                ).reverse(),
              },
              yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
              series: [
                {
                  name: 'CPU',
                  type: 'line',
                  data: data.snapshots.map((s) => s.cpu_pct).reverse(),
                  color: '#38BDF8',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
                {
                  name: 'Memory',
                  type: 'line',
                  data: data.snapshots.map((s) => s.mem_pct).reverse(),
                  color: '#A78BFA',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
                {
                  name: 'Disk',
                  type: 'line',
                  data: data.snapshots.map((s) => s.disk_pct).reverse(),
                  color: '#34D399',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
              ],
            }}
          />
        )}
      </GlassCard>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  suffix,
  extra,
  loading,
}: {
  icon: React.ElementType;
  label: string;
  value?: number | null;
  suffix?: string;
  extra?: string;
  loading: boolean;
}) {
  const pct = value ?? 0;
  const color = pct >= 90 ? 'text-red-400' : pct >= 75 ? 'text-amber-400' : 'text-emerald-400';
  const barColor = pct >= 90 ? '#F87171' : pct >= 75 ? '#FBBF24' : '#34D399';

  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} className="text-slate-400" />
        <span className="text-xs text-slate-500 uppercase tracking-wider">{label}</span>
      </div>
      {loading ? (
        <Skeleton className="h-8 w-20" />
      ) : (
        <>
          <p className={`text-3xl font-bold ${color}`}>
            {value != null ? Math.round(value) : '—'}{value != null ? suffix : ''}
          </p>
          {extra && <p className="text-[10px] text-slate-500 mt-1">{extra}</p>}
          <div className="h-1.5 rounded-full bg-white/[0.06] mt-2 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${Math.min(pct, 100)}%`, background: barColor }}
            />
          </div>
        </>
      )}
    </GlassCard>
  );
}
