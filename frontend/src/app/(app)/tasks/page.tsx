'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cable, Check, Inbox, Lock, RefreshCw, X } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { SectionHeader } from '@/components/layout/SectionHeader';
import { Badge } from '@/components/ui/Badge';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { get, patch, post } from '@/lib/api';
import { STATE_TEXT } from '@/lib/status';
import { cn, timeAgo } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';

interface PortTask {
  id: number;
  host_id: number;
  host_name: string;
  host_hostname: string;
  port: number;
  protocol: string;
  service: string | null;
  status: string;
  first_seen: string | null;
  last_seen: string | null;
}

interface SslTask {
  id: number;
  host_id: number;
  host_name: string;
  host_hostname: string;
  port: number;
  protocol: string;
  service: string | null;
  ssl_issuer: string | null;
  ssl_subject: string | null;
  ssl_expiry_days: number | null;
  ssl_expiry_date: string | null;
  ssl_status: string;
  first_seen: string | null;
  last_seen: string | null;
}

interface TasksData {
  port_tasks: PortTask[];
  ssl_tasks: SslTask[];
  summary: {
    new_ports: number;
    new_ssl: number;
    total_pending: number;
  };
}

type Item = { hostId: number; portId: number };

function HostCell({ id, name, hostname }: { id: number; name: string; hostname: string }) {
  return (
    <Td className="py-1.5 pl-5">
      <Link prefetch={false} href={`/hosts/${id}`} className="font-medium text-fg hover:text-accent">{name}</Link>
      <span className="block font-mono text-meta text-fg-3">{hostname}</span>
    </Td>
  );
}

function StatusLabel({ status }: { status: string }) {
  if (status === 'new') return <Badge tone="accent">New</Badge>;
  if (status === 'monitored') {
    return <span className="inline-flex items-center gap-1 text-meta text-fg-2"><Check size={12} aria-hidden="true" /> Monitored</span>;
  }
  if (status === 'dismissed') return <span className="text-meta text-fg-3">Dismissed</span>;
  return <span className="text-meta text-fg-3">{status}</span>;
}

function RowActions({ busy, onMonitor, onDismiss, what }: { busy: boolean; onMonitor: () => void; onDismiss: () => void; what: string }) {
  return (
    <div className="flex items-center justify-end gap-1.5">
      <Button size="sm" variant="secondary" disabled={busy} onClick={onMonitor} aria-label={`Monitor ${what}`}>
        <Check size={13} aria-hidden="true" /> Monitor
      </Button>
      <Button size="sm" variant="ghost" disabled={busy} onClick={onDismiss} aria-label={`Dismiss ${what}`}>
        <X size={13} aria-hidden="true" /> Dismiss
      </Button>
    </div>
  );
}

export default function TasksPage() {
  useEffect(() => { document.title = 'Discovery | Nodeglow'; }, []);

  const qc = useQueryClient();
  const toast = useToastStore();
  const [bulkBusy, setBulkBusy] = useState(false);
  const query = useQuery<TasksData>({
    queryKey: ['tasks'],
    queryFn: () => get('/api/tasks'),
    refetchInterval: 30_000,
  });
  const data = query.data;

  const actionMut = useMutation({
    mutationFn: ({ hostId, portId, action }: Item & { action: string }) =>
      patch(`/hosts/api/${hostId}/discovered-ports/${portId}`, { action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['nav-counts'] });
      toast.show('Updated', 'success');
    },
    onError: () => toast.show('Action failed', 'error'),
  });

  const scanAllMut = useMutation({
    mutationFn: () => post('/api/tasks/scan-all'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['nav-counts'] });
      toast.show('Port scan completed for all hosts', 'success');
    },
    onError: () => toast.show('Scan failed', 'error'),
  });

  const bulkAction = async (items: Item[], action: string) => {
    setBulkBusy(true);
    let done = 0;
    try {
      for (const item of items) {
        await patch(`/hosts/api/${item.hostId}/discovered-ports/${item.portId}`, { action });
        done += 1;
      }
      toast.show(`${items.length} items updated`, 'success');
    } catch {
      toast.show(`Stopped after ${done} of ${items.length} items — the next one failed`, 'error');
    } finally {
      setBulkBusy(false);
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['nav-counts'] });
    }
  };

  const newPorts = (data?.port_tasks ?? []).filter((p) => p.status === 'new');
  const newSsl = (data?.ssl_tasks ?? []).filter((s) => s.ssl_status === 'new');
  const resolvedPorts = (data?.port_tasks ?? []).filter((p) => p.status !== 'new');
  const resolvedSsl = (data?.ssl_tasks ?? []).filter((s) => s.ssl_status !== 'new');
  const totalPending = newPorts.length + newSsl.length;
  const monitored = resolvedPorts.filter((p) => p.status === 'monitored').length + resolvedSsl.filter((s) => s.ssl_status === 'monitored').length;
  const dismissed = resolvedPorts.filter((p) => p.status === 'dismissed').length + resolvedSsl.filter((s) => s.ssl_status === 'dismissed').length;
  const busy = actionMut.isPending || bulkBusy;

  return (
    <div>
      <PageHeader
        title="Discovery"
        description={
          data
            ? `${totalPending} new item${totalPending === 1 ? '' : 's'} to review · ports and certificates found by scans · updated ${formatAsOf(query.dataUpdatedAt)}`
            : 'Ports and certificates found by scans, waiting for a decision'
        }
        actions={
          <Button onClick={() => scanAllMut.mutate()} loading={scanAllMut.isPending}>
            {!scanAllMut.isPending && <RefreshCw size={14} aria-hidden="true" />}
            {scanAllMut.isPending ? 'Scanning…' : 'Scan all hosts'}
          </Button>
        }
      />

      <QueryState
        query={query}
        errorTitle="Could not load discoveries"
        loading={
          <div className="space-y-4" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-[96px] w-full rounded-card" />
            <Skeleton className="h-[240px] w-full rounded-card" />
          </div>
        }
      >
        {(d) => (
          <div className="space-y-4">
            <Card>
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
                <BigNumber size="sm" value={d.summary.new_ports} label="New ports" />
                <BigNumber size="sm" value={d.summary.new_ssl} label="New certificates" />
                <BigNumber size="sm" value={monitored} label="Monitored" />
                <BigNumber size="sm" value={dismissed} label="Dismissed" />
              </div>
            </Card>

            {totalPending === 0 && (
              <Card>
                <EmptyState
                  variant="confirmed"
                  icon={Inbox}
                  title="Inbox empty — nothing to review"
                  description="Port scans run automatically every 6 hours; new ports and certificates appear here."
                  asOf={formatAsOf(query.dataUpdatedAt)}
                />
              </Card>
            )}

            {newPorts.length > 0 && (
              <Card as="section" padding="none" aria-labelledby="h-ports">
                <div className="px-5 pt-5">
                  <CardHeader
                    title={<span className="inline-flex items-center gap-2"><Cable size={15} aria-hidden="true" className="text-fg-3" /> Discovered ports <Badge tone="accent" className="num">{newPorts.length} new</Badge></span>}
                    titleId="h-ports"
                    actions={newPorts.length > 1 && (
                      <>
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => bulkAction(newPorts.map((p) => ({ hostId: p.host_id, portId: p.id })), 'monitor_port')}>
                          <Check size={13} aria-hidden="true" /> Monitor all
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => bulkAction(newPorts.map((p) => ({ hostId: p.host_id, portId: p.id })), 'dismiss_port')}>
                          <X size={13} aria-hidden="true" /> Dismiss all
                        </Button>
                      </>
                    )}
                  />
                </div>
                <TableContainer className="relative">
                  <Table>
                    <THead>
                      <Tr>
                        <Th className="pl-5">Host</Th>
                        <Th numeric>Port</Th>
                        <Th>Service</Th>
                        <Th className="max-md:hidden">First seen</Th>
                        <Th className="pr-5 text-right"><span className="sr-only">Actions</span></Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {newPorts.map((p) => (
                        <Tr key={`port-${p.id}`}>
                          <HostCell id={p.host_id} name={p.host_name} hostname={p.host_hostname} />
                          <Td numeric className="font-mono"><span className="text-fg">{p.port}</span><span className="text-meta text-fg-3">/{p.protocol}</span></Td>
                          <Td>{p.service ? <Badge>{p.service}</Badge> : <span className="text-fg-3">—</span>}</Td>
                          <Td muted className="text-meta max-md:hidden">{p.first_seen ? timeAgo(p.first_seen) : '—'}</Td>
                          <Td className="pr-5">
                            <RowActions
                              busy={busy}
                              what={`port ${p.port} on ${p.host_name}`}
                              onMonitor={() => actionMut.mutate({ hostId: p.host_id, portId: p.id, action: 'monitor_port' })}
                              onDismiss={() => actionMut.mutate({ hostId: p.host_id, portId: p.id, action: 'dismiss_port' })}
                            />
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </TableContainer>
              </Card>
            )}

            {newSsl.length > 0 && (
              <Card as="section" padding="none" aria-labelledby="h-ssl">
                <div className="px-5 pt-5">
                  <CardHeader
                    title={<span className="inline-flex items-center gap-2"><Lock size={15} aria-hidden="true" className="text-fg-3" /> Discovered certificates <Badge tone="accent" className="num">{newSsl.length} new</Badge></span>}
                    titleId="h-ssl"
                    actions={newSsl.length > 1 && (
                      <>
                        <Button size="sm" variant="secondary" disabled={busy} onClick={() => bulkAction(newSsl.map((s) => ({ hostId: s.host_id, portId: s.id })), 'monitor_ssl')}>
                          <Check size={13} aria-hidden="true" /> Monitor all
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => bulkAction(newSsl.map((s) => ({ hostId: s.host_id, portId: s.id })), 'dismiss_ssl')}>
                          <X size={13} aria-hidden="true" /> Dismiss all
                        </Button>
                      </>
                    )}
                  />
                </div>
                <TableContainer className="relative">
                  <Table>
                    <THead>
                      <Tr>
                        <Th className="pl-5">Host</Th>
                        <Th numeric>Port</Th>
                        <Th>Subject</Th>
                        <Th className="max-lg:hidden">Issuer</Th>
                        <Th numeric>Expires in</Th>
                        <Th className="pr-5 text-right"><span className="sr-only">Actions</span></Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {newSsl.map((s) => {
                        const st = s.ssl_expiry_days == null ? null : s.ssl_expiry_days <= 14 ? 'down' : s.ssl_expiry_days <= 30 ? 'warning' : null;
                        return (
                          <Tr key={`ssl-${s.id}`}>
                            <HostCell id={s.host_id} name={s.host_name} hostname={s.host_hostname} />
                            <Td numeric className="font-mono">{s.port}</Td>
                            <Td className="max-w-[220px] truncate font-mono text-meta" title={s.ssl_subject ?? undefined}>{s.ssl_subject || '—'}</Td>
                            <Td muted className="max-w-[220px] truncate text-meta max-lg:hidden" title={s.ssl_issuer ?? undefined}>{s.ssl_issuer || '—'}</Td>
                            <Td numeric className={cn('font-medium', st ? STATE_TEXT[st] : 'text-fg')}>
                              {s.ssl_expiry_days != null ? `${s.ssl_expiry_days} d` : <span className="text-fg-3">—</span>}
                            </Td>
                            <Td className="pr-5">
                              <RowActions
                                busy={busy}
                                what={`certificate on ${s.host_name}:${s.port}`}
                                onMonitor={() => actionMut.mutate({ hostId: s.host_id, portId: s.id, action: 'monitor_ssl' })}
                                onDismiss={() => actionMut.mutate({ hostId: s.host_id, portId: s.id, action: 'dismiss_ssl' })}
                              />
                            </Td>
                          </Tr>
                        );
                      })}
                    </TBody>
                  </Table>
                </TableContainer>
              </Card>
            )}

            {(resolvedPorts.length > 0 || resolvedSsl.length > 0) && (
              <div>
                <SectionHeader title="History" subtitle={`${resolvedPorts.length + resolvedSsl.length} decided items`} />
                <Card padding="none">
                  <TableContainer className="relative">
                    <Table density="compact">
                      <THead>
                        <Tr>
                          <Th className="pl-5">Host</Th>
                          <Th numeric>Port</Th>
                          <Th>Type</Th>
                          <Th>Decision</Th>
                          <Th className="pr-5 max-sm:hidden">First seen</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {[
                          ...resolvedPorts.map((p) => ({ key: `rp-${p.id}`, host_id: p.host_id, host_name: p.host_name, port: `${p.port}/${p.protocol}`, type: 'Port', status: p.status, first_seen: p.first_seen })),
                          ...resolvedSsl.map((s) => ({ key: `rs-${s.id}`, host_id: s.host_id, host_name: s.host_name, port: `${s.port}/${s.protocol}`, type: 'Certificate', status: s.ssl_status, first_seen: s.first_seen })),
                        ].map((r) => (
                          <Tr key={r.key}>
                            <Td className="pl-5"><Link prefetch={false} href={`/hosts/${r.host_id}`} className="text-fg-2 hover:text-accent">{r.host_name}</Link></Td>
                            <Td numeric muted className="font-mono text-meta">{r.port}</Td>
                            <Td muted className="text-meta">{r.type}</Td>
                            <Td><StatusLabel status={r.status} /></Td>
                            <Td muted className="pr-5 text-meta max-sm:hidden">{r.first_seen ? timeAgo(r.first_seen) : '—'}</Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableContainer>
                </Card>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </div>
  );
}
