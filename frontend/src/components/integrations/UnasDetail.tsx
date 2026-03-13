'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatUptime } from '@/lib/utils';

interface UnasSystem {
  hostname: string;
  version: string;
  uptime_s: number;
  cpu_pct: number;
  temp_c: number;
  mem_used_gb: number;
  mem_total_gb: number;
  mem_pct: number;
}

interface UnasDisk {
  name: string;
  model: string;
  size_gb: number;
  temp: number;
  status: string;
  status_label: string;
  ok: boolean;
  smart_ok: boolean;
  type: string;
  life_span?: number;
  power_on_hrs?: number;
}

interface UnasRaid {
  name: string;
  type_label: string;
  state: string;
  healthy: boolean;
  size_gb: number;
  used_gb: number;
  pct: number;
  active_devices: number;
  failed_devices: number;
}

interface UnasPool {
  name: string;
  size_gb: number;
  used_gb: number;
  free_gb: number;
  pct: number;
  healthy: boolean;
}

interface UnasTotals {
  disks_total: number;
  disks_ok: number;
  disks_error: number;
  disks_hot: number;
  raids_total: number;
  raids_healthy: number;
  pools_total: number;
  storage_used_gb: number;
  storage_total_gb: number;
  storage_pct: number;
}

interface UnasData {
  system: UnasSystem;
  disks: UnasDisk[];
  raids: UnasRaid[];
  storage_pools: UnasPool[];
  totals: UnasTotals;
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

export function UnasDetail({ data }: { data: UnasData }) {
  const { system, disks, raids, storage_pools, totals } = data;

  return (
    <div className="space-y-6">
      {/* System info */}
      <GlassCard className="p-5">
        <h3 className="text-sm font-medium text-slate-300 mb-4">System Information</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-8 gap-y-3">
          <div>
            <p className="text-xs text-slate-500">Hostname</p>
            <p className="text-sm text-slate-200">{system.hostname}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Version</p>
            <p className="text-sm text-slate-200">{system.version}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Uptime</p>
            <p className="text-sm text-slate-200">{formatUptime(system.uptime_s)}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Temperature</p>
            <p className="text-sm text-slate-200">{system.temp_c}&deg;C</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Memory</p>
            <p className="text-sm text-slate-200">{system.mem_used_gb.toFixed(1)} / {system.mem_total_gb.toFixed(1)} GB</p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
          <ProgressBar label="CPU" pct={system.cpu_pct} />
          <ProgressBar label="Memory" pct={system.mem_pct} />
        </div>
      </GlassCard>

      {/* Disk table */}
      {disks && disks.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">
              Disks ({totals.disks_ok}/{totals.disks_total} OK
              {totals.disks_error > 0 && <span className="text-red-400">, {totals.disks_error} error</span>}
              {totals.disks_hot > 0 && <span className="text-amber-400">, {totals.disks_hot} hot</span>})
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Model</th>
                  <th className="px-4 py-2 text-left">Type</th>
                  <th className="px-4 py-2 text-right">Size</th>
                  <th className="px-4 py-2 text-right">Temp</th>
                  <th className="px-4 py-2 text-center">Status</th>
                  <th className="px-4 py-2 text-center">SMART</th>
                  <th className="px-4 py-2 text-right">Power-On</th>
                </tr>
              </thead>
              <tbody>
                {disks.map((d) => (
                  <tr key={d.name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-200">{d.name}</td>
                    <td className="px-4 py-2 text-slate-400">{d.model}</td>
                    <td className="px-4 py-2">
                      <Badge>{d.type}</Badge>
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400">{d.size_gb.toFixed(0)} GB</td>
                    <td className="px-4 py-2 text-right text-slate-400">{d.temp}&deg;C</td>
                    <td className="px-4 py-2 text-center">
                      <span className={d.ok ? 'text-emerald-400' : 'text-red-400'}>
                        {d.status_label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-center">
                      <span className={d.smart_ok ? 'text-emerald-400' : 'text-red-400'}>
                        {d.smart_ok ? 'OK' : 'FAIL'}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {d.power_on_hrs != null ? `${d.power_on_hrs.toLocaleString()} h` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* RAID status cards */}
      {raids && raids.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">
            RAID Arrays ({totals.raids_healthy}/{totals.raids_total} healthy)
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {raids.map((r) => (
              <GlassCard key={r.name} className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{r.name}</span>
                  <div className="flex items-center gap-2">
                    <Badge>{r.type_label}</Badge>
                    <span className={`text-xs font-medium ${r.healthy ? 'text-emerald-400' : 'text-red-400'}`}>
                      {r.state}
                    </span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                  <span>Active devices</span><span className="text-slate-300">{r.active_devices}</span>
                  <span>Failed devices</span>
                  <span className={r.failed_devices > 0 ? 'text-red-400' : 'text-slate-300'}>
                    {r.failed_devices}
                  </span>
                </div>
                <ProgressBar
                  label="Usage"
                  pct={r.pct}
                  detail={`${r.used_gb.toFixed(1)} / ${r.size_gb.toFixed(1)} GB`}
                />
              </GlassCard>
            ))}
          </div>
        </div>
      )}

      {/* Storage pool usage bars */}
      {storage_pools && storage_pools.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Storage Pools</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {storage_pools.map((p) => (
              <GlassCard key={p.name} className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{p.name}</span>
                  <span className={`text-xs font-medium ${p.healthy ? 'text-emerald-400' : 'text-red-400'}`}>
                    {p.healthy ? 'healthy' : 'degraded'}
                  </span>
                </div>
                <ProgressBar
                  label="Usage"
                  pct={p.pct}
                  detail={`${p.used_gb.toFixed(1)} / ${p.size_gb.toFixed(1)} GB (${p.free_gb.toFixed(1)} GB free)`}
                />
              </GlassCard>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
