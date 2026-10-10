'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Cpu, Eye, EyeOff, Monitor, Plus, Radio, Terminal, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Switch } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { SegmentedControl } from '@/components/ui/Tabs';
import { CopyButton } from '@/components/ui/CopyButton';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { useAgents } from '@/hooks/queries/useAgents';
import { useHostsV1 } from '@/hooks/queries/useHosts';
import { get, del, patch, apiErrorMessage } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { timeAgo } from '@/lib/utils';
import type { Agent } from '@/types';
import { UsageBar, agentState } from './_components/agentStatus';

interface EnrollmentInfo {
  enrollment_key: string;
  server_url: string;
  install_linux: string;
  install_windows: string;
}

function mask(secret: string) {
  if (secret.length <= 4) return '••••••••';
  return `${'•'.repeat(Math.min(16, secret.length - 4))}${secret.slice(-4)}`;
}

function AddAgentDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [platform, setPlatform] = useState<'linux' | 'windows'>('linux');
  const [showKey, setShowKey] = useState(false);
  const query = useQuery({
    queryKey: ['enrollment-info'],
    queryFn: () => get<EnrollmentInfo>('/api/enrollment-info'),
    enabled: open,
    staleTime: 60_000,
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Add agent"
      description="Run one of these commands on the target machine. The agent installs itself and enrols against this server."
      footer={<Button variant="secondary" onClick={onClose}>Done</Button>}
    >
      <QueryState
        query={query}
        errorTitle="Could not load the install command"
        loading={
          <div className="space-y-3" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-20 w-full" />
          </div>
        }
      >
        {(info) => {
          const command = platform === 'linux' ? info.install_linux : info.install_windows;
          return (
            <div className="space-y-4">
              <SegmentedControl
                label="Target platform"
                value={platform}
                onChange={setPlatform}
                options={[
                  { value: 'linux', label: <><Terminal size={13} aria-hidden="true" /> Linux</> },
                  { value: 'windows', label: <><Monitor size={13} aria-hidden="true" /> Windows</> },
                ]}
              />
              <div className="relative">
                <pre
                  aria-label={`${platform === 'linux' ? 'Linux' : 'Windows'} install command`}
                  className="overflow-x-auto whitespace-pre-wrap break-all rounded-ctl border border-border bg-surface-2 p-4 pr-10 font-mono text-meta text-fg"
                >
                  {command}
                </pre>
                <div className="absolute right-2 top-2">
                  <CopyButton text={command} />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 pt-1 sm:grid-cols-2">
                <div className="min-w-0">
                  <p className="ng-label">Server URL</p>
                  <code className="block truncate rounded-chip bg-surface-2 px-2 py-1 font-mono text-meta text-fg">
                    {info.server_url}
                  </code>
                </div>
                <div className="min-w-0">
                  <p className="ng-label">Enrollment key</p>
                  <div className="flex items-center gap-1">
                    <code
                      className="block flex-1 truncate rounded-chip bg-surface-2 px-2 py-1 font-mono text-meta text-fg"
                      aria-label={showKey ? 'Enrollment key' : 'Enrollment key (hidden)'}
                    >
                      {showKey ? info.enrollment_key : mask(info.enrollment_key)}
                    </code>
                    <IconButton
                      size="sm"
                      aria-label={showKey ? 'Hide enrollment key' : 'Show enrollment key'}
                      aria-pressed={showKey}
                      onClick={() => setShowKey((v) => !v)}
                    >
                      {showKey ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
                    </IconButton>
                    <CopyButton text={info.enrollment_key} />
                  </div>
                </div>
              </div>
            </div>
          );
        }}
      </QueryState>
    </Modal>
  );
}

export default function AgentsPage() {
  useEffect(() => { document.title = 'Agents & probes | Nodeglow'; }, []);
  const agentsQuery = useAgents();
  const { data: agents } = agentsQuery;
  const { data: hosts } = useHostsV1();
  const [showAdd, setShowAdd] = useState(false);
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [togglingProbe, setTogglingProbe] = useState<number | null>(null);

  // Number of hosts each probe is currently responsible for.
  const hostCountByProbe = new Map<number, number>();
  for (const h of hosts ?? []) {
    if (h.probe_id != null) {
      hostCountByProbe.set(h.probe_id, (hostCountByProbe.get(h.probe_id) ?? 0) + 1);
    }
  }

  async function handleDelete(agentId: number, name: string) {
    const ok = await confirm({ title: 'Decommission agent', description: `Decommission agent "${name}"? This will also remove its associated host and all snapshots.`, confirmLabel: 'Decommission', variant: 'danger' });
    if (!ok) return;
    try {
      await del(`/api/v1/agents/${agentId}`);
      qc.invalidateQueries({ queryKey: ['agents'] });
      toast('Agent decommissioned', 'success');
    } catch {
      toast('Failed to delete agent', 'error');
    }
  }

  async function handleToggleProbe(agent: Agent, next: boolean) {
    setTogglingProbe(agent.id);
    try {
      const res = await patch<{ is_probe?: boolean }>(`/api/v1/agents/${agent.id}`, { is_probe: next });
      // Verify the server actually stored the change (audit F-07): check the
      // PATCH response if it carries the field, otherwise the refreshed list.
      let applied: boolean | undefined = res && typeof res === 'object' && 'is_probe' in res ? !!res.is_probe : undefined;
      const fresh = await get<Agent[]>('/api/v1/agents');
      qc.setQueryData(['agents'], fresh);
      if (applied === undefined) applied = !!fresh.find((a) => a.id === agent.id)?.is_probe;
      if (applied !== next) {
        toast(`Probe mode for ${agent.name} was not saved by the server`, 'error');
        return;
      }
      toast(
        next ? `${agent.name} now checks hosts in its network as a probe` : `${agent.name} is no longer a probe`,
        'success',
      );
    } catch (e) {
      toast(apiErrorMessage(e, 'Failed to update probe mode'), 'error');
    } finally {
      setTogglingProbe(null);
    }
  }

  const list = agents ?? [];
  const online = list.filter((a) => agentState(a).status === 'ok').length;
  const offline = list.filter((a) => agentState(a).status === 'down').length;
  const probes = list.filter((a) => a.is_probe).length;
  const asOf = formatAsOf(agentsQuery.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Agents & probes"
        description={
          agents
            ? `${list.length} agent${list.length === 1 ? '' : 's'} · ${online} online${offline ? ` · ${offline} offline` : ''}${probes ? ` · ${probes} probe${probes === 1 ? '' : 's'}` : ''}${asOf ? ` · updated ${asOf}` : ''}`
            : 'Deployed monitoring agents and remote probes'
        }
        actions={
          <Button onClick={() => setShowAdd(true)}>
            <Plus size={16} aria-hidden="true" />
            Add agent
          </Button>
        }
      />

      <AddAgentDialog open={showAdd} onClose={() => setShowAdd(false)} />

      <Card padding="none">
        <QueryState
          query={agentsQuery}
          loading={
            <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading agents">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              icon={Cpu}
              title="No agents registered"
              description="Install the Nodeglow agent on a Linux or Windows host to collect CPU, memory, disk, and network metrics. Agents auto-enrol against this server."
              action={
                <Button size="sm" onClick={() => setShowAdd(true)}>
                  <Terminal size={14} aria-hidden="true" /> Show install command
                </Button>
              }
            />
          }
        >
          {(rows) => (
            <TableContainer>
              <Table className="min-w-[980px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Agent</Th>
                    <Th>Platform</Th>
                    <Th className="w-[120px]">CPU</Th>
                    <Th className="w-[120px]">Memory</Th>
                    <Th className="w-[120px]">Disk</Th>
                    <Th>Last seen</Th>
                    <Th>Probe mode</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((agent) => {
                    const st = agentState(agent);
                    const stale = st.status !== 'ok';
                    const detailHref = agent.host_id ? `/hosts/${agent.host_id}` : `/agents/${agent.id}`;
                    const hostCount = hostCountByProbe.get(agent.id) ?? 0;
                    return (
                      <Tr key={agent.id}>
                        <Td><StatusPill status={st.status}>{st.label}</StatusPill></Td>
                        <Td className="max-w-[260px]">
                          <Link href={detailHref} className="block truncate font-medium text-fg hover:text-accent">
                            {agent.name}
                          </Link>
                          <span className="block truncate font-mono text-meta text-fg-3">{agent.hostname ?? '—'}</span>
                        </Td>
                        <Td>
                          <div className="flex flex-wrap items-center gap-1">
                            <Badge>{agent.platform ?? 'Unknown'}</Badge>
                            {agent.agent_version && <Badge>v{agent.agent_version}</Badge>}
                          </div>
                        </Td>
                        <Td><UsageBar label="CPU" value={agent.cpu_pct} stale={stale} /></Td>
                        <Td><UsageBar label="Memory" value={agent.mem_pct} stale={stale} /></Td>
                        <Td><UsageBar label="Disk" value={agent.disk_pct} stale={stale} /></Td>
                        <Td muted className="whitespace-nowrap">
                          {agent.last_seen ? (
                            <time dateTime={agent.last_seen} title={new Date(agent.last_seen).toLocaleString()}>
                              {timeAgo(agent.last_seen)}
                            </time>
                          ) : (
                            <span className="text-fg-3">Never</span>
                          )}
                        </Td>
                        <Td>
                          <div className="flex items-center gap-2">
                            <Switch
                              checked={!!agent.is_probe}
                              onChange={(v) => handleToggleProbe(agent, v)}
                              disabled={togglingProbe === agent.id}
                              aria-label={`Probe mode for ${agent.name}`}
                            />
                            {agent.is_probe && (
                              st.status === 'ok' ? (
                                <span className="inline-flex items-center gap-1 whitespace-nowrap text-meta text-fg-2">
                                  <Radio size={12} aria-hidden="true" />
                                  <span className="num">{hostCount}</span> host{hostCount === 1 ? '' : 's'}
                                </span>
                              ) : (
                                // A silent probe leaves its hosts unobserved: they are "no data", never healthy.
                                <StatusPill status="unknown" size="sm">
                                  {hostCount} host{hostCount === 1 ? '' : 's'} unobserved
                                </StatusPill>
                              )
                            )}
                          </div>
                        </Td>
                        <Td className="text-right">
                          <IconButton
                            size="sm"
                            aria-label={`Decommission ${agent.name}`}
                            title="Decommission"
                            onClick={() => handleDelete(agent.id, agent.name)}
                          >
                            <Trash2 size={14} aria-hidden="true" />
                          </IconButton>
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>
      {ConfirmDialogElement}
    </div>
  );
}
