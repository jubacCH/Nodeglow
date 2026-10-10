'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Wrench } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { Tag } from '@/components/ui/Tag';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, get, post } from '@/lib/api';
import { formatDateTime } from '@/lib/incidents';
import { useIsEditor } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import type { MaintenanceWindowRef } from '@/types';

interface MaintenanceHost {
  id: number;
  name: string;
  hostname: string;
  source: string;
  maintenance: boolean;
  maintenance_manual?: boolean;
  maintenance_window?: MaintenanceWindowRef | null;
  maintenance_until?: string | null;
}

export const MAINTENANCE_HOSTS_KEY = ['maintenance-hosts'];

/** Why the host is in maintenance and until when. */
function reason(h: MaintenanceHost): string {
  const parts: string[] = [];
  if (h.maintenance_manual !== false && (h.maintenance_until || !h.maintenance_window)) {
    parts.push(h.maintenance_until ? `Manual · until ${formatDateTime(h.maintenance_until)}` : 'Manual · no end set');
  }
  if (h.maintenance_window) {
    const end = h.maintenance_window.ends_at ? ` · until ${formatDateTime(h.maintenance_window.ends_at)}` : '';
    parts.push(`Window "${h.maintenance_window.name}"${end}`);
  }
  return parts.join('; ');
}

const isManual = (h: MaintenanceHost) => h.maintenance_manual !== false;

/** Hosts currently in maintenance (manual flag or an active window). */
export function MaintenanceHostsCard() {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const canEdit = useIsEditor();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [busyId, setBusyId] = useState<number | null>(null);
  const query = useQuery({
    queryKey: MAINTENANCE_HOSTS_KEY,
    queryFn: () => get<MaintenanceHost[]>('/api/v1/hosts?status=maintenance'),
    refetchInterval: 60_000,
  });

  async function removeMaintenance(h: MaintenanceHost) {
    const ok = await confirm({
      title: 'End maintenance',
      description: `End maintenance for "${h.name}"? It is checked and alerted on again right away.`,
      confirmLabel: 'End maintenance',
    });
    if (!ok) return;
    setBusyId(h.id);
    try {
      await post(`/hosts/api/${h.id}/maintenance`);
      toast(`${h.name} left maintenance`, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not end maintenance'), 'error');
    } finally {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: MAINTENANCE_HOSTS_KEY });
      qc.invalidateQueries({ queryKey: ['hosts'] });
    }
  }

  const action = (h: MaintenanceHost) =>
    canEdit && isManual(h) ? (
      <Button variant="secondary" size="sm" loading={busyId === h.id} onClick={() => removeMaintenance(h)} aria-label={`End maintenance for ${h.name}`}>
        End maintenance
      </Button>
    ) : null;

  return (
    <Card as="section" padding="none" aria-labelledby="maint-hosts-heading">
      <div className="px-[20px] pt-[18px] max-[759px]:px-[16px]">
        <CardHeader title="Hosts in maintenance" titleId="maint-hosts-heading" meta={query.data ? `${query.data.length}` : undefined} />
      </div>
      <QueryState
        query={query}
        compact
        errorTitle="Could not load hosts in maintenance"
        loading={<div className="space-y-2 px-[20px] pb-[20px]" aria-busy="true" aria-label="Loading">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9 w-full" />)}</div>}
        empty={
          <EmptyState compact icon={Wrench} title="No hosts in maintenance"
            description="Hosts put into maintenance by hand or by an active window appear here." />
        }
      >
        {(hosts) => (
          <>
            <TableContainer className="max-[759px]:hidden">
              <Table aria-labelledby="maint-hosts-heading">
                <THead>
                  <Tr>
                    <Th className="w-[300px] pl-[20px]">Host</Th>
                    <Th>Reason</Th>
                    <Th className="w-[110px]">Source</Th>
                    {canEdit && <Th className="w-[170px]"><span className="sr-only">Actions</span></Th>}
                  </Tr>
                </THead>
                <TBody>
                  {hosts.map((h) => (
                    <Tr key={h.id}>
                      <Td className="max-w-0 py-2 pl-[20px]">
                        <span className="flex min-w-0 items-center gap-2">
                          <StatusDot status="maint" label="Maintenance" />
                          <Link prefetch={false} href={`/hosts/${h.id}`} className="truncate font-medium text-fg hover:text-accent">{h.name}</Link>
                        </span>
                        <p className="truncate pl-4 font-mono text-meta text-fg-3">{h.hostname}</p>
                      </Td>
                      <Td muted>{reason(h)}</Td>
                      <Td><Tag>{h.source}</Tag></Td>
                      {canEdit && <Td className="text-right">{action(h)}</Td>}
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
            <ul className="divide-y divide-border border-t border-border min-[760px]:hidden">
              {hosts.map((h) => (
                <li key={h.id} className="px-[16px] py-3">
                  <div className="flex items-center gap-2">
                    <StatusDot status="maint" label="Maintenance" />
                    <Link prefetch={false} href={`/hosts/${h.id}`} className="min-w-0 truncate font-medium text-fg hover:text-accent">{h.name}</Link>
                    <Tag className="ml-auto">{h.source}</Tag>
                  </div>
                  <p className="mt-0.5 truncate font-mono text-meta text-fg-3">{h.hostname}</p>
                  <p className="mt-1 text-meta text-fg-2">{reason(h)}</p>
                  {action(h) && <div className="mt-2">{action(h)}</div>}
                </li>
              ))}
            </ul>
          </>
        )}
      </QueryState>
      {ConfirmDialogElement}
    </Card>
  );
}
