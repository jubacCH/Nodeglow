'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { Cpu, Plus, Radio, Terminal, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Switch } from '@/components/ui/Field';
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
import { AddAgentDialog } from './_components/AddAgentDialog';

export default function AgentsPage() {
  useEffect(() => { document.title = 'Agents & probes | Nodeglow'; }, []);
  const agentsQuery = useAgents();
  const { data: agents } = agentsQuery;
  const { data: hosts } = useHostsV1();
  // ?add=1 opens the dialog directly (the first-run welcome links here).
  const searchParams = useSearchParams();
  const [showAdd, setShowAdd] = useState(() => searchParams.get('add') === '1');
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

      <AddAgentDialog open={showAdd} onClose={() => setShowAdd(false)} agents={agents} />

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
            <TableContainer className="relative">
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
                        <Td><UsageBar hideLabel label="CPU" value={agent.cpu_pct} stale={stale} /></Td>
                        <Td><UsageBar hideLabel label="Memory" value={agent.mem_pct} stale={stale} /></Td>
                        <Td><UsageBar hideLabel label="Disk" value={agent.disk_pct} stale={stale} /></Td>
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
