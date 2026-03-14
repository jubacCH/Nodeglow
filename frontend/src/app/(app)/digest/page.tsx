'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { useEffect } from 'react';
import type { Digest } from '@/types';
import { AlertTriangle, Server, FileText } from 'lucide-react';
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
        description={data ? `${data.period_start} — ${data.period_end}` : 'Loading...'}
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
          value={data?.syslog.total}
          color="text-sky-400"
          loading={isLoading}
        />
        <SummaryCard
          icon={FileText}
          label="Syslog Errors"
          value={data?.syslog.errors}
          color={data?.syslog.errors ? 'text-red-400' : 'text-emerald-400'}
          loading={isLoading}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Top Incidents */}
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
          {data?.incidents.mttr_minutes != null && (
            <p className="text-xs text-slate-500 mt-3 pt-3 border-t border-white/[0.04]">
              Mean Time to Resolve: {Math.round(data.incidents.mttr_minutes)}min
            </p>
          )}
        </GlassCard>

        {/* Worst Hosts */}
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
