'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { formatUptime } from '@/lib/utils';

interface SynologySystem {
  model: string;
  dsm_version: string;
  uptime_s: number;
  mem_pct: number;
  cpu_pct: number;
  temp_c: number;
}

interface SynologyPool {
  name: string;
  status: string;
  used_pct: number;
  used_human: string;
  total_human: string;
}

interface SynologyData {
  system: SynologySystem;
  storage_pools: SynologyPool[];
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function ProgressBar({ label, pct, detail }: { label: string; pct: number; detail?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span>{detail ?? `${pct.toFixed(1)}%`}</span>
      </div>
      <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${barColor(pct)}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  );
}

function poolStatusColor(status: string): string {
  if (status === 'normal' || status === 'healthy') return 'text-emerald-400';
  if (status === 'degraded') return 'text-amber-400';
  return 'text-red-400';
}

export function SynologyDetail({ data }: { data: SynologyData }) {
  const { system, storage_pools } = data;

  return (
    <div className="space-y-6">
      {/* System info */}
      <GlassCard className="p-5">
        <h3 className="text-sm font-medium text-slate-300 mb-4">System Information</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-8 gap-y-3">
          <div>
            <p className="text-xs text-slate-500">Model</p>
            <p className="text-sm text-slate-200">{system.model}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">DSM Version</p>
            <p className="text-sm text-slate-200">{system.dsm_version}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Uptime</p>
            <p className="text-sm text-slate-200">{formatUptime(system.uptime_s)}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Temperature</p>
            <p className="text-sm text-slate-200">{system.temp_c}&deg;C</p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <ProgressBar label="CPU" pct={system.cpu_pct} />
          <ProgressBar label="Memory" pct={system.mem_pct} />
        </div>
      </GlassCard>

      {/* Storage pools */}
      {storage_pools && storage_pools.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Storage Pools</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {storage_pools.map((pool) => (
              <GlassCard key={pool.name} className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{pool.name}</span>
                  <span className={`text-xs font-medium ${poolStatusColor(pool.status)}`}>
                    {pool.status}
                  </span>
                </div>
                <ProgressBar
                  label="Usage"
                  pct={pool.used_pct}
                  detail={`${pool.used_human} / ${pool.total_human}`}
                />
              </GlassCard>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
