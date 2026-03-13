'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';

interface PortainerContainer {
  name: string;
  image: string;
  status: string;
  state: string;
  ports: string;
  created: string;
}

interface PortainerStack {
  name: string;
  type: string;
  status: string;
}

interface PortainerData {
  containers: PortainerContainer[];
  stacks: PortainerStack[];
}

function stateColor(state: string): string {
  switch (state) {
    case 'running': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
    case 'stopped':
    case 'exited': return 'bg-red-500/20 text-red-400 border-red-500/30';
    case 'paused': return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
    default: return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function PortainerDetail({ data }: { data: PortainerData }) {
  const { containers, stacks } = data;
  const running = (containers ?? []).filter((c) => c.state === 'running').length;
  const stopped = (containers ?? []).filter((c) => c.state === 'stopped' || c.state === 'exited').length;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Containers" value={(containers ?? []).length} />
        <StatCard label="Running" value={running} />
        <StatCard label="Stopped" value={stopped} />
        <StatCard label="Stacks" value={(stacks ?? []).length} />
      </div>

      {/* Container table */}
      {containers && containers.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Containers</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Image</th>
                  <th className="px-4 py-2 text-left">State</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-left">Ports</th>
                </tr>
              </thead>
              <tbody>
                {containers.map((c, i) => (
                  <tr key={`${c.name}-${i}`} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-200 font-medium">{c.name}</td>
                    <td className="px-4 py-2 text-slate-400 font-mono text-xs truncate max-w-[200px]">{c.image}</td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium border ${stateColor(c.state)}`}>
                        {c.state}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-400 text-xs">{c.status}</td>
                    <td className="px-4 py-2 text-slate-400 font-mono text-xs">{c.ports || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Stacks */}
      {stacks && stacks.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Stacks</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Type</th>
                  <th className="px-4 py-2 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {stacks.map((s, i) => (
                  <tr key={`${s.name}-${i}`} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-200">{s.name}</td>
                    <td className="px-4 py-2"><Badge>{s.type}</Badge></td>
                    <td className="px-4 py-2 text-slate-400">{s.status}</td>
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
