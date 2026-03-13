'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useHost, useHostHistory } from '@/hooks/queries/useHosts';
import { formatLatency, uptimeColor } from '@/lib/utils';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

type Tab = 'overview' | 'results' | 'syslog';

export default function HostDetailPage() {
  const params = useParams();
  const hostId = Number(params.id);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const { data: host, isLoading } = useHost(hostId);
  const { data: history, isLoading: historyLoading } = useHostHistory(hostId, 24);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'results', label: 'Check Results' },
    { key: 'syslog', label: 'Syslog' },
  ];

  const hostStatus = !host
    ? 'unknown' as const
    : !host.enabled
      ? 'disabled' as const
      : host.maintenance
        ? 'maintenance' as const
        : host.latest?.success
          ? 'online' as const
          : host.latest?.success === false
            ? 'offline' as const
            : 'unknown' as const;

  return (
    <div>
      <PageHeader
        title={isLoading ? 'Loading...' : (host?.name ?? 'Host')}
        description={host?.hostname}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/hosts">
              <Button variant="ghost" size="sm">
                <ArrowLeft size={16} />
                Back
              </Button>
            </Link>
            <Button variant="ghost" size="sm">
              <RefreshCw size={16} />
            </Button>
          </div>
        }
      />

      {/* Host header card */}
      <GlassCard className="p-4 mb-6">
        {isLoading ? (
          <div className="flex items-center gap-4">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="flex-1">
              <Skeleton className="h-5 w-48 mb-2" />
              <Skeleton className="h-4 w-32" />
            </div>
          </div>
        ) : host ? (
          <div className="flex items-center gap-4">
            <StatusDot status={hostStatus} pulse={hostStatus === 'offline'} className="w-4 h-4" />
            <div className="flex-1">
              <p className="text-lg font-semibold text-slate-100">{host.name}</p>
              <p className="text-sm text-slate-400 font-mono">{host.hostname}</p>
            </div>
            <div className="flex items-center gap-3">
              <Badge>{host.check_type}</Badge>
              <Badge>{host.source}</Badge>
              {host.maintenance && <Badge variant="severity" severity="warning">Maintenance</Badge>}
            </div>
          </div>
        ) : (
          <p className="text-slate-400">Host not found</p>
        )}
      </GlassCard>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-sky-400 border-b-2 border-sky-400'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Uptime stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {isLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <GlassCard key={i} className="p-4">
                  <Skeleton className="h-4 w-12 mb-2" />
                  <Skeleton className="h-8 w-20" />
                </GlassCard>
              ))
            ) : (
              <>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">24h Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.h24 ?? null)}`}>
                    {host?.uptime.h24 != null ? `${host.uptime.h24.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">7d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.d7 ?? null)}`}>
                    {host?.uptime.d7 != null ? `${host.uptime.d7.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">30d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.d30 ?? null)}`}>
                    {host?.uptime.d30 != null ? `${host.uptime.d30.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
              </>
            )}
          </div>

          {/* Latency */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-2">Current Latency</h3>
            {isLoading ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <p className="text-2xl font-mono text-slate-100">
                {formatLatency(host?.latest?.latency_ms ?? null)}
              </p>
            )}
          </GlassCard>

          {/* Heatmap placeholder */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Availability Heatmap</h3>
            <Skeleton className="h-20 w-full" />
          </GlassCard>

          {/* Latency chart placeholder */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Latency Chart</h3>
            <Skeleton className="h-40 w-full" />
          </GlassCard>
        </div>
      )}

      {activeTab === 'results' && (
        <GlassCard>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Timestamp</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Latency</th>
                </tr>
              </thead>
              <tbody>
                {historyLoading &&
                  Array.from({ length: 10 }).map((_, i) => (
                    <tr key={i} className="border-b border-white/[0.03]">
                      <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    </tr>
                  ))}
                {history?.results.map((r) => (
                  <tr key={r.id} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3 text-xs text-slate-400 font-mono">
                      {new Date(r.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <StatusDot status={r.success ? 'online' : 'offline'} />
                        <span className="text-xs text-slate-300">{r.success ? 'OK' : 'Fail'}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {formatLatency(r.latency_ms)}
                    </td>
                  </tr>
                ))}
                {!historyLoading && (!history?.results || history.results.length === 0) && (
                  <tr>
                    <td colSpan={3} className="px-4 py-8 text-center text-sm text-slate-500">
                      No check results available
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {activeTab === 'syslog' && (
        <GlassCard className="p-6">
          <p className="text-sm text-slate-400">Syslog entries for this host will appear here.</p>
          <Skeleton className="h-32 w-full mt-4" />
        </GlassCard>
      )}
    </div>
  );
}
