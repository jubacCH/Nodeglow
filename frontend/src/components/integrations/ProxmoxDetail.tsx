'use client';

import { useState } from 'react';
import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { CopyButton } from '@/components/ui/CopyButton';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { apiErrorMessage, post } from '@/lib/api';
import { unknownHostKey, type HostKeyQuestion } from '@/lib/agentDeploy';
import { DeployRunStatus } from '@/components/agents/DeployRunStatus';
import { HostKeyPrompt } from '@/components/agents/HostKeyPrompt';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import Link from 'next/link';
import { SectionTitle, StatGrid, StatTile, StateLabel, TableCard, UsageBar, fixed, isNum, uptime } from './parts';

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

interface BackupFile {
  volid?: string;
  vmid?: number;
  size?: number;
  ctime?: number;
  content?: string;
  notes?: string;
}

interface ProxmoxData {
  totals: ProxmoxTotals;
  nodes: ProxmoxNode[];
  vms: ProxmoxGuest[];
  containers: ProxmoxGuest[];
  /** null: the backup storages could not be read (see the page notice). */
  backups?: Record<string, BackupFile[]> | null;
  tasks?: Array<{ type?: string; status?: string; starttime?: number; node?: string; id?: string }>;
}

/** POST .../deploy-agent: a run to follow (ssh) or a script for the node (script). */
interface DeployResult {
  ok: boolean;
  mode?: 'ssh' | 'script' | 'none';
  run_id?: string;
  results?: { vmid: number; name: string }[];
  manual_script?: string | null;
  skipped_self?: string | null;
  message?: string;
}

function formatSize(bytes: number | undefined | null): string {
  if (!bytes) return '—';
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

function timeAgo(epoch: number | undefined | null): string {
  if (!epoch) return '—';
  const diff = Date.now() / 1000 - epoch;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  const days = Math.floor(diff / 86400);
  return `${days}d ago`;
}

function backupAge(epoch: number | undefined | null): { text: string; severity: 'ok' | 'warn' | 'old' } {
  if (!epoch) return { text: 'Never', severity: 'old' };
  const hours = (Date.now() / 1000 - epoch) / 3600;
  if (hours < 36) return { text: timeAgo(epoch), severity: 'ok' };
  if (hours < 72) return { text: timeAgo(epoch), severity: 'warn' };
  return { text: timeAgo(epoch), severity: 'old' };
}

const AGE_TEXT = { ok: 'text-ok', warn: 'text-warning', old: 'text-down' } as const;

/** "3/5" when both parts are known, otherwise null (→ "—"). */
function ratio(a: unknown, b: unknown): string | null {
  return isNum(a) && isNum(b) ? `${a}/${b}` : null;
}

function nodeState(online: boolean | null | undefined): HealthState {
  if (online === true) return 'ok';
  if (online === false) return 'down';
  return 'unknown';
}

/** Stopped guests are usually intentional: dimmed, not red. */
function GuestStatus({ status }: { status: string | null | undefined }) {
  if (status === 'running') return <StateLabel status="ok">running</StateLabel>;
  if (status === 'stopped') return <StateLabel status="disabled">stopped</StateLabel>;
  if (!status) return <StateLabel status="unknown">No data</StateLabel>;
  return <StateLabel status="degraded">{status}</StateLabel>;
}

function gb(used: unknown, total: unknown): string | undefined {
  return isNum(used) && isNum(total) ? `${used.toFixed(1)} / ${total.toFixed(1)} GB` : undefined;
}

export function ProxmoxDetail({ data, configId }: { data: ProxmoxData; configId?: number }) {
  const { totals, nodes, vms, containers } = data;
  const allGuests = [
    ...(vms ?? []).map((v) => ({ ...v, guestType: 'VM' as const })),
    ...(containers ?? []).map((c) => ({ ...c, guestType: 'LXC' as const })),
  ];

  // Build backup lookup: vmid -> latest backup info
  const backupsByVmid: Record<number, { ctime: number; size: number; storage: string }> = {};
  if (data.backups) {
    for (const [storage, files] of Object.entries(data.backups)) {
      for (const f of files ?? []) {
        if (f.vmid && f.ctime) {
          const existing = backupsByVmid[f.vmid];
          if (!existing || f.ctime > existing.ctime) {
            backupsByVmid[f.vmid] = { ctime: f.ctime, size: f.size ?? 0, storage };
          }
        }
      }
    }
  }

  const [deploying, setDeploying] = useState(false);
  const [deployResult, setDeployResult] = useState<DeployResult | null>(null);
  const [hostKey, setHostKey] = useState<HostKeyQuestion | null>(null);

  async function deployAgents(fingerprint?: string) {
    if (!configId) return;
    setDeploying(true);
    if (!fingerprint) setDeployResult(null);
    try {
      const res = await post<DeployResult>(
        `/api/v1/integrations/proxmox/${configId}/deploy-agent`,
        fingerprint ? { host_key_fingerprint: fingerprint } : {},
      );
      setHostKey(null);
      setDeployResult(res);
    } catch (e) {
      const asked = unknownHostKey(e);
      if (asked && !fingerprint) {
        setHostKey(asked);
      } else {
        setHostKey(null);
        setDeployResult({ ok: false, message: apiErrorMessage(e, 'The deploy could not be started.') });
      }
    } finally {
      setDeploying(false);
    }
  }

  const backupCount = Object.keys(backupsByVmid).length;

  return (
    <div className="space-y-6">
      {/* Stat tiles */}
      <StatGrid cols={5}>
        <StatTile label="Nodes online" value={ratio(totals?.nodes_online, totals?.nodes_total)} />
        <StatTile label="VMs running" value={ratio(totals?.vms_running, totals?.vms_total)} />
        <StatTile label="LXCs running" value={ratio(totals?.lxc_running, totals?.lxc_total)} />
        <StatTile label="CPU avg" value={fixed(totals?.cpu_avg_pct)} unit="%" />
        <StatTile label="Memory" value={fixed(totals?.mem_pct)} unit="%" />
      </StatGrid>

      {/* Nodes */}
      <section>
        <SectionTitle>Nodes</SectionTitle>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {(nodes ?? []).map((node) => (
            <Card key={node.name} padding="sm" className="space-y-3">
              <div className="flex min-w-0 items-center gap-2">
                <StatusDot status={nodeState(node.online)} />
                <span className="truncate text-ui font-medium text-fg">{node.name}</span>
                <span className="num ml-auto shrink-0 text-meta text-fg-3">{node.online ? uptime(node.uptime_s) ?? '—' : 'Offline'}</span>
              </div>
              {/* An offline node reports zeros: show "not reported", not 0 %. */}
              <UsageBar label="CPU" pct={node.online ? node.cpu_pct : null} />
              <UsageBar label="Memory" pct={node.online ? node.mem_pct : null} detail={node.online ? gb(node.mem_used_gb, node.mem_total_gb) : undefined} />
              <UsageBar label="Disk" pct={node.online ? node.disk_pct : null} detail={node.online ? gb(node.disk_used_gb, node.disk_total_gb) : undefined} />
            </Card>
          ))}
        </div>
      </section>

      {/* Backup summary */}
      {backupCount > 0 && (
        <Card as="section" padding="sm">
          <CardHeader title="Backup status" meta={`${backupCount} VMs/LXCs with backups`} className="mb-3" />
          <div className="flex flex-wrap gap-2">
            {(() => {
              const now = Date.now() / 1000;
              const fresh = Object.values(backupsByVmid).filter(b => (now - b.ctime) < 36 * 3600).length;
              const aging = Object.values(backupsByVmid).filter(b => (now - b.ctime) >= 36 * 3600 && (now - b.ctime) < 72 * 3600).length;
              const old = Object.values(backupsByVmid).filter(b => (now - b.ctime) >= 72 * 3600).length;
              return (
                <>
                  {fresh > 0 && <StatusPill status="ok">{fresh} current</StatusPill>}
                  {aging > 0 && <StatusPill status="warning">{aging} aging</StatusPill>}
                  {old > 0 && <StatusPill status="down">{old} outdated</StatusPill>}
                </>
              );
            })()}
          </div>
        </Card>
      )}

      {/* VMs + Containers table */}
      {allGuests.length > 0 && (
        <TableCard title="VMs & containers" meta={`${allGuests.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Type</Th>
                <Th>ID</Th>
                <Th>Name</Th>
                <Th>Node</Th>
                <Th>Last backup</Th>
                <Th numeric>Uptime</Th>
              </Tr>
            </THead>
            <TBody>
              {allGuests.map((g) => {
                const bk = g.id ? backupsByVmid[g.id] : undefined;
                const age = bk ? backupAge(bk.ctime) : null;
                return (
                  <Tr key={`${g.guestType}-${g.id}`}>
                    <Td><GuestStatus status={g.status} /></Td>
                    <Td><Badge>{g.guestType}</Badge></Td>
                    <Td muted className="num">{g.id}</Td>
                    <Td className="max-w-[240px] truncate">
                      <Link href={'/hosts?q=' + encodeURIComponent(g.name)} className="text-accent hover:underline">{g.name}</Link>
                    </Td>
                    <Td muted>{g.node || '—'}</Td>
                    <Td className="whitespace-nowrap">
                      {data.backups === null ? (
                        <span className="text-meta text-fg-3" title="Backups could not be read">—</span>
                      ) : !bk || !age ? (
                        <span className="text-meta text-fg-3">No backup</span>
                      ) : (
                        <span className="flex items-center gap-1.5">
                          <span className={cn('text-meta', AGE_TEXT[age.severity])}>{age.text}</span>
                          <span className="num text-micro text-fg-3">{formatSize(bk.size)}</span>
                        </span>
                      )}
                    </Td>
                    <Td numeric muted className="whitespace-nowrap">
                      {isNum(g.uptime_s) && g.uptime_s > 0 ? uptime(g.uptime_s) : '—'}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Deploy agent */}
      {configId && (
        <Card as="section">
          <CardHeader
            title="Agent deployment"
            actions={
              <Button size="sm" loading={deploying} onClick={() => deployAgents()} disabled={Boolean(hostKey)}>
                {deploying ? 'Installing…' : 'Install agent on all LXCs'}
              </Button>
            }
            className="flex-wrap"
          />
          <p className="mb-3 text-ui text-fg-2">
            Installs the Nodeglow agent on all running LXCs. The agent collects system metrics, system logs and Docker
            container logs, enrolls itself and updates automatically. Add an SSH key in the Proxmox configuration for
            automatic installation.
          </p>

          {hostKey && (
            <HostKeyPrompt
              question={hostKey}
              busy={deploying}
              onTrust={() => deployAgents(hostKey.fingerprint)}
              onCancel={() => setHostKey(null)}
            />
          )}

          {deployResult && (
            <div className="space-y-3" aria-live="polite">
              {/* SSH mode: follow the run */}
              {deployResult.mode === 'ssh' && deployResult.run_id && (
                <>
                  {deployResult.skipped_self && (
                    <p className="text-meta text-fg-3">Skipped {deployResult.skipped_self}: Nodeglow runs there.</p>
                  )}
                  <DeployRunStatus runId={deployResult.run_id} />
                </>
              )}

              {/* Script mode: LXC list + copyable script */}
              {deployResult.mode === 'script' && (
                <>
                  {(deployResult.results?.length ?? 0) > 0 && (
                    <div className="rounded-ctl border border-border-2 bg-surface-2 p-3">
                      <p className="mb-2 text-meta text-fg-2">
                        {deployResult.results?.length} running LXC(s). Add an SSH key in the Proxmox configuration to install automatically.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {deployResult.results?.map((r) => (
                          <Badge key={r.vmid}>
                            CT {r.vmid} <span className="text-fg-3">{r.name}</span>
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {deployResult.manual_script && (
                    <div>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="text-meta text-fg-2">Paste into the Proxmox node shell:</span>
                        <CopyButton text={deployResult.manual_script} />
                      </div>
                      <pre className="overflow-x-auto whitespace-pre rounded-ctl border border-border bg-surface-2 p-3 font-mono text-micro text-fg-2">
                        {deployResult.manual_script}
                      </pre>
                    </div>
                  )}
                </>
              )}

              {/* No mode (message only) */}
              {(!deployResult.mode || deployResult.mode === 'none') && deployResult.message && (
                <div className="rounded-ctl border border-warning/30 bg-warning-soft p-3 text-ui text-warning" role="alert">
                  {deployResult.message}
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
