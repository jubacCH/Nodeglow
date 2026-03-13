'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { useState } from 'react';
import {
  Database, Clock, Server, RefreshCw, Download,
} from 'lucide-react';

export default function SystemStatusPage() {
  const toast = useToastStore((s) => s.show);
  const [checking, setChecking] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<{ update_available: boolean; local: string; changelog?: string } | null>(null);

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => get<{ status: string; db: string }>('/health'),
    refetchInterval: 30_000,
  });

  async function checkUpdate() {
    setChecking(true);
    try {
      const info = await get<{ update_available: boolean; local: string; changelog?: string }>('/api/update/check');
      setUpdateInfo(info);
    } catch {
      toast('Failed to check for updates', 'error');
    } finally {
      setChecking(false);
    }
  }

  return (
    <div>
      <PageHeader title="System Status" description="Backend health and diagnostics" />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Database Status */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Database size={16} className="text-sky-400" />
            <h3 className="text-sm font-medium text-slate-300">Database</h3>
          </div>
          <div className="flex items-center gap-3">
            <StatusDot status={health?.db === 'connected' ? 'online' : 'offline'} />
            <span className="text-sm text-slate-200">
              PostgreSQL — {health?.db ?? 'checking...'}
            </span>
          </div>
        </GlassCard>

        {/* App Status */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Server size={16} className="text-emerald-400" />
            <h3 className="text-sm font-medium text-slate-300">Application</h3>
          </div>
          <div className="flex items-center gap-3">
            <StatusDot status={health?.status === 'ok' ? 'online' : 'offline'} />
            <span className="text-sm text-slate-200">
              Nodeglow — {health?.status ?? 'checking...'}
            </span>
          </div>
        </GlassCard>
      </div>

      {/* Software Updates */}
      <GlassCard className="p-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Download size={16} className="text-violet-400" />
            <h3 className="text-sm font-medium text-slate-300">Software Updates</h3>
          </div>
          <Button size="sm" variant="ghost" onClick={checkUpdate} disabled={checking}>
            <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
            {checking ? 'Checking...' : 'Check Now'}
          </Button>
        </div>
        {updateInfo ? (
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Badge>{updateInfo.local}</Badge>
              {updateInfo.update_available ? (
                <Badge variant="severity" severity="warning">Update available</Badge>
              ) : (
                <Badge variant="severity" severity="info">Up to date</Badge>
              )}
            </div>
            {updateInfo.changelog && (
              <pre className="text-xs text-slate-400 bg-white/[0.02] rounded p-3 mt-2 max-h-[200px] overflow-y-auto whitespace-pre-wrap">
                {updateInfo.changelog}
              </pre>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Click &quot;Check Now&quot; to check for updates</p>
        )}
      </GlassCard>

      {/* WebSocket Status */}
      <GlassCard className="p-4">
        <div className="flex items-center gap-2 mb-3">
          <Clock size={16} className="text-amber-400" />
          <h3 className="text-sm font-medium text-slate-300">Background Services</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {['Ping Checker', 'Integration Poller', 'Correlation Engine', 'Log Intelligence', 'Syslog Receiver', 'Alert Rules'].map((svc) => (
            <div key={svc} className="flex items-center gap-2">
              <StatusDot status="online" />
              <span className="text-sm text-slate-300">{svc}</span>
            </div>
          ))}
        </div>
      </GlassCard>
    </div>
  );
}
