'use client';

import { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { formatUptime } from '@/lib/utils';
import { post } from '@/lib/api';
import { ScrollText, CheckCircle, XCircle, Copy, Check } from 'lucide-react';
import Link from 'next/link';

interface ProxmoxTotals {
  nodes_online: number;
  nodes_total: number;
  vms_running: number;
  vms_total: number;
  lxc_running: number;
  lxc_total: number;
  cpu_avg_pct: number;
  mem_pct: number;
  mem_used_gb: number;
  mem_total_gb: number;
}

interface ProxmoxNode {
  name: string;
  online: boolean;
  uptime_s: number;
  cpu_pct: number;
  mem_used_gb: number;
  mem_total_gb: number;
  mem_pct: number;
  disk_used_gb: number;
  disk_total_gb: number;
  disk_pct: number;
}

interface ProxmoxGuest {
  id: number;
  vmid?: number;
  name: string;
  status: string;
  node: string;
  cpu: number;
  memory: number;
  disk: number;
  uptime_s: number;
}

interface ProxmoxData {
  totals: ProxmoxTotals;
  nodes: ProxmoxNode[];
  vms: ProxmoxGuest[];
  containers: ProxmoxGuest[];
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

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function guestStatusColor(status: string): string {
  if (status === 'running') return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
  if (status === 'stopped') return 'bg-red-500/20 text-red-400 border-red-500/30';
  return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
}

interface DeployResult {
  ok: boolean;
  deployed: number;
  failed: number;
  results: { vmid: number; name: string; status: string; error?: string }[];
  manual_script?: string;
  syslog_target?: string;
  message?: string;
}

export function ProxmoxDetail({ data, configId }: { data: ProxmoxData; configId?: number }) {
  const { totals, nodes, vms, containers } = data;
  const allGuests = [
    ...(vms ?? []).map((v) => ({ ...v, guestType: 'VM' as const })),
    ...(containers ?? []).map((c) => ({ ...c, guestType: 'LXC' as const })),
  ];

  const [deploying, setDeploying] = useState(false);
  const [deployResult, setDeployResult] = useState<DeployResult | null>(null);
  const [copied, setCopied] = useState(false);

  async function deploySyslog() {
    if (!configId) return;
    setDeploying(true);
    setDeployResult(null);
    try {
      const res = await post<DeployResult>(`/api/v1/integrations/proxmox/${configId}/deploy-syslog`, {});
      setDeployResult(res);
    } catch {
      setDeployResult({ ok: false, deployed: 0, failed: 0, results: [], manual_script: undefined, message: 'Request failed' });
    } finally {
      setDeploying(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Nodes" value={`${totals.nodes_online}/${totals.nodes_total}`} />
        <StatCard label="VMs Running" value={`${totals.vms_running}/${totals.vms_total}`} />
        <StatCard label="LXCs Running" value={`${totals.lxc_running}/${totals.lxc_total}`} />
        <StatCard label="CPU Avg" value={`${totals.cpu_avg_pct.toFixed(1)}%`} />
        <StatCard label="Memory" value={`${totals.mem_pct.toFixed(1)}%`} />
      </div>

      {/* Nodes */}
      <div>
        <h3 className="text-sm font-medium text-slate-300 mb-3">Nodes</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(nodes ?? []).map((node) => (
            <GlassCard key={node.name} className="p-4 space-y-3">
              <div className="flex items-center gap-2">
                <StatusDot status={node.online ? 'online' : 'offline'} />
                <span className="text-sm font-medium text-slate-200">{node.name}</span>
                <span className="ml-auto text-xs text-slate-500">{formatUptime(node.uptime_s)}</span>
              </div>
              <ProgressBar label="CPU" pct={node.cpu_pct} />
              <ProgressBar label="Memory" pct={node.mem_pct} detail={`${node.mem_used_gb.toFixed(1)} / ${node.mem_total_gb.toFixed(1)} GB`} />
              <ProgressBar label="Disk" pct={node.disk_pct} detail={`${node.disk_used_gb.toFixed(1)} / ${node.disk_total_gb.toFixed(1)} GB`} />
            </GlassCard>
          ))}
        </div>
      </div>

      {/* VMs + Containers table */}
      {allGuests.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">VMs &amp; Containers</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Type</th>
                  <th className="px-4 py-2 text-left">ID</th>
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-left">Node</th>
                  <th className="px-4 py-2 text-right">Uptime</th>
                </tr>
              </thead>
              <tbody>
                {allGuests.map((g) => (
                  <tr key={`${g.guestType}-${g.id}`} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2">
                      <Badge>{g.guestType}</Badge>
                    </td>
                    <td className="px-4 py-2 text-slate-400">{g.id}</td>
                    <td className="px-4 py-2 text-slate-200"><Link href={'/hosts?q=' + encodeURIComponent(g.name)} className="text-sky-400 hover:underline">{g.name}</Link></td>
                    <td className="px-4 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium border ${guestStatusColor(g.status)}`}>
                        {g.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-400">{g.node}</td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {g.uptime_s > 0 ? formatUptime(g.uptime_s) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Deploy Syslog */}
      {configId && (
        <GlassCard className="p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <ScrollText size={16} className="text-sky-400" />
              <h3 className="text-sm font-medium text-slate-300">Syslog Forwarding</h3>
            </div>
            <Button size="sm" disabled={deploying} onClick={deploySyslog}>
              {deploying ? 'Deploying...' : 'Deploy to all LXCs'}
            </Button>
          </div>
          <p className="text-xs text-slate-500 mb-3">
            Deploys to all running LXCs: <span className="text-slate-400">rsyslog</span> (system logs) + <span className="text-slate-400">Docker daemon config</span> (container logs).
            Docker is restarted to apply — running containers will briefly restart.
          </p>

          {deployResult && (
            <div className="space-y-3">
              {/* Summary */}
              <div className={`p-3 rounded-lg text-sm ${deployResult.failed === 0 && deployResult.deployed > 0 ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-300' : deployResult.deployed > 0 ? 'bg-amber-500/10 border border-amber-500/20 text-amber-300' : 'bg-red-500/10 border border-red-500/20 text-red-300'}`}>
                {deployResult.message || `Deployed: ${deployResult.deployed} | Failed: ${deployResult.failed}`}
                {deployResult.syslog_target && (
                  <span className="text-xs text-slate-500 ml-2">→ {deployResult.syslog_target}</span>
                )}
              </div>

              {/* Per-LXC results */}
              {deployResult.results.length > 0 && (
                <div className="space-y-1 max-h-[200px] overflow-y-auto">
                  {deployResult.results.map((r) => (
                    <div key={r.vmid} className="flex items-center gap-2 text-xs py-1 px-2 rounded hover:bg-white/[0.02]">
                      {r.status === 'ok' ? (
                        <CheckCircle size={12} className="text-emerald-400 shrink-0" />
                      ) : (
                        <XCircle size={12} className="text-red-400 shrink-0" />
                      )}
                      <span className="text-slate-400 w-12">CT {r.vmid}</span>
                      <span className="text-slate-200 flex-1">{r.name}</span>
                      {r.error && <span className="text-red-400 text-[10px] truncate max-w-[200px]">{r.error}</span>}
                    </div>
                  ))}
                </div>
              )}

              {/* Manual script fallback */}
              {deployResult.manual_script && (
                <div className="mt-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-slate-400">Manual fallback — run on Proxmox node:</span>
                    <button
                      className="text-xs text-sky-400 hover:text-sky-300 flex items-center gap-1"
                      onClick={() => {
                        navigator.clipboard.writeText(deployResult.manual_script!);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      }}
                    >
                      {copied ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy</>}
                    </button>
                  </div>
                  <pre className="text-[11px] text-slate-300 font-mono bg-black/30 rounded-md p-3 overflow-x-auto whitespace-pre">
                    {deployResult.manual_script}
                  </pre>
                </div>
              )}
            </div>
          )}
        </GlassCard>
      )}
    </div>
  );
}
