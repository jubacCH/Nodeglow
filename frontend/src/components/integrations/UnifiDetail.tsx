'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatUptime } from '@/lib/utils';

interface UnifiDevice {
  name: string;
  model: string;
  mac: string;
  ip: string;
  uptime_s: number;
  clients: number;
  type: string;
}

interface UnifiClient {
  hostname: string;
  mac: string;
  ip: string;
  signal: number;
  speed: number;
  uptime_s: number;
  is_wired: boolean;
}

interface UnifiNetwork {
  total_devices: number;
  wired_clients: number;
  wireless_clients: number;
}

interface UnifiData {
  devices: UnifiDevice[];
  clients: UnifiClient[];
  network: UnifiNetwork;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function signalBadge(signal: number): string {
  if (signal >= -50) return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
  if (signal >= -70) return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
  return 'bg-red-500/20 text-red-400 border-red-500/30';
}

export function UnifiDetail({ data }: { data: UnifiData }) {
  const { devices, clients, network } = data;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <StatCard label="Devices" value={network?.total_devices ?? devices?.length ?? 0} />
        <StatCard label="Wired Clients" value={network?.wired_clients ?? 0} />
        <StatCard label="Wireless Clients" value={network?.wireless_clients ?? 0} />
      </div>

      {/* Device cards */}
      {devices && devices.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Devices</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {devices.map((d) => (
              <GlassCard key={d.mac} className="p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{d.name || d.mac}</span>
                  <Badge>{d.type}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                  <span>Model</span><span className="text-slate-300">{d.model}</span>
                  <span>IP</span><span className="text-slate-300 font-mono">{d.ip}</span>
                  <span>MAC</span><span className="text-slate-300 font-mono">{d.mac}</span>
                  <span>Clients</span><span className="text-slate-300">{d.clients}</span>
                  <span>Uptime</span><span className="text-slate-300">{formatUptime(d.uptime_s)}</span>
                </div>
              </GlassCard>
            ))}
          </div>
        </div>
      )}

      {/* Client table */}
      {clients && clients.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Clients ({clients.length})</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">MAC</th>
                  <th className="px-4 py-2 text-left">IP</th>
                  <th className="px-4 py-2 text-left">Type</th>
                  <th className="px-4 py-2 text-right">Signal</th>
                  <th className="px-4 py-2 text-right">Speed</th>
                  <th className="px-4 py-2 text-right">Uptime</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <tr key={c.mac} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-200">{c.hostname || '—'}</td>
                    <td className="px-4 py-2 font-mono text-slate-400">{c.mac}</td>
                    <td className="px-4 py-2 font-mono text-slate-400">{c.ip}</td>
                    <td className="px-4 py-2">
                      <Badge>{c.is_wired ? 'Wired' : 'WiFi'}</Badge>
                    </td>
                    <td className="px-4 py-2 text-right">
                      {c.is_wired ? (
                        <span className="text-slate-500">—</span>
                      ) : (
                        <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium border ${signalBadge(c.signal)}`}>
                          {c.signal} dBm
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {c.speed ? `${c.speed} Mbps` : '—'}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {c.uptime_s > 0 ? formatUptime(c.uptime_s) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
