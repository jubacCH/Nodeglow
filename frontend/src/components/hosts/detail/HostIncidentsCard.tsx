'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { get } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import type { HealthState } from '@/lib/status';
import type { Incident } from '@/types';

type HostIncident = Incident & { acknowledged?: boolean };

function dotFor(inc: HostIncident): HealthState {
  if (inc.status === 'resolved') return 'ok';
  if (inc.severity === 'critical') return 'down';
  if (inc.severity === 'warning') return 'warning';
  return 'degraded';
}

const STATUS_LABEL: Record<Incident['status'], string> = { open: 'Open', acknowledged: 'Acknowledged', resolved: 'Resolved' };

/** Incidents whose recorded hosts include this host (GET /api/v1/incidents?host_id=…). */
export function HostIncidentsCard({ hostId, className }: { hostId: number; className?: string }) {
  const query = useQuery({
    queryKey: ['host-incidents', hostId],
    queryFn: () => get<HostIncident[]>(`/api/v1/incidents?host_id=${hostId}&status=all&sort=updated&limit=10`),
    enabled: hostId > 0,
    refetchInterval: 60_000,
  });
  const open = (query.data ?? []).filter((i) => i.status !== 'resolved').length;
  return (
    <Card as="section" aria-labelledby="host-incidents-title" className={className}>
      <CardHeader
        title="Incidents"
        titleId="host-incidents-title"
        meta={query.data ? (open ? `${open} open` : 'none open') : undefined}
        actions={<Link href="/alerts" className="text-accent hover:text-accent-hover">All incidents</Link>}
      />
      <QueryState
        query={query}
        compact
        empty={
          <EmptyState
            compact
            variant="confirmed"
            title="No incidents for this host"
            description="Incidents opened before hosts were recorded on them are not listed here."
            asOf={formatAsOf(query.dataUpdatedAt)}
          />
        }
      >
        {(items) => (
          <>
            <ul className="-mx-2 space-y-0.5">
              {items.map((inc) => (
                <li key={inc.id}>
                  <Link
                    href={`/incidents/${inc.id}`}
                    className="flex items-start gap-3 rounded-ng-sm px-2 py-2 hover:bg-surface-2"
                  >
                    <span className="mt-[6px]">
                      <StatusDot
                        status={dotFor(inc)}
                        size="md"
                        glow={inc.status !== 'resolved'}
                        breathe={inc.status === 'open' && inc.severity === 'critical'}
                        label={`${inc.severity}, ${STATUS_LABEL[inc.status]}`}
                      />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-ui text-fg">{inc.title}</span>
                      <span className="block truncate text-meta text-fg-3">
                        {STATUS_LABEL[inc.status]} · {inc.rule} · {timeAgo(inc.status === 'resolved' && inc.resolved_at ? inc.resolved_at : inc.created_at)}
                      </span>
                    </span>
                    <Badge variant="severity" severity={inc.severity}>{inc.severity}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-micro text-fg-3">Incidents opened before hosts were recorded on them are not listed.</p>
          </>
        )}
      </QueryState>
    </Card>
  );
}
