'use client';

import Link from 'next/link';
import { CheckCircle2, Eye } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { cn } from '@/lib/utils';
import {
  formatAgo, formatDateTime, formatDuration, incidentDurationMs, incidentNeedsGlow, ruleLabel,
  type IncidentItem, type IncidentSort,
} from '@/lib/incidents';
import { AffectedHosts, IncidentStatusBadge, SeverityIndicator } from './IncidentBits';

export interface IncidentRowActions {
  canEdit: boolean;
  /** Id of the incident whose action is in flight. */
  pendingId: number | null;
  onAcknowledge: (inc: IncidentItem) => void;
  onResolve: (inc: IncidentItem) => void;
}

function Actions({ inc, actions, compact }: { inc: IncidentItem; actions: IncidentRowActions; compact?: boolean }) {
  if (!actions.canEdit || inc.status === 'resolved') return null;
  const busy = actions.pendingId === inc.id;
  return (
    <div className={cn('flex items-center gap-1', compact ? 'justify-start' : 'justify-end')}>
      {inc.status === 'open' && (
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => actions.onAcknowledge(inc)} aria-label={`Acknowledge ${inc.title}`}>
          <Eye size={13} aria-hidden="true" /> Ack
        </Button>
      )}
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => actions.onResolve(inc)} aria-label={`Resolve ${inc.title}`}>
        <CheckCircle2 size={13} aria-hidden="true" /> Resolve
      </Button>
    </div>
  );
}

function Age({ inc, now }: { inc: IncidentItem; now: number }) {
  const ms = incidentDurationMs(inc, now);
  if (inc.status === 'resolved') {
    return (
      <span title={`Opened ${formatDateTime(inc.created_at)} · resolved ${formatDateTime(inc.resolved_at)}`}>
        <span className="text-fg-2">Lasted </span>{ms === null ? '—' : formatDuration(ms)}
      </span>
    );
  }
  return <span title={`Opened ${formatDateTime(inc.created_at)}`}>{ms === null ? '—' : formatDuration(ms)}</span>;
}

/** Incident list: table from 760 px, cards below. */
export function IncidentTable({ items, sort, onSort, actions, now }: {
  items: IncidentItem[];
  sort: IncidentSort;
  onSort: (s: IncidentSort) => void;
  actions: IncidentRowActions;
  now: number;
}) {
  return (
    <>
      <Card padding="none" className="overflow-hidden max-[759px]:hidden">
        <TableContainer>
          <Table aria-label="Incidents">
            <THead>
              <Tr>
                <Th className="w-[120px]" sort={sort === 'severity' ? 'desc' : 'none'} onSort={() => onSort('severity')}>Severity</Th>
                <Th>Incident</Th>
                <Th className="w-[130px]">Status</Th>
                <Th className="w-[260px]">Affected hosts</Th>
                <Th numeric className="w-[110px]" sort={sort === 'created' ? 'desc' : 'none'} onSort={() => onSort('created')}>Age</Th>
                <Th numeric className="w-[110px]" sort={sort === 'updated' ? 'desc' : 'none'} onSort={() => onSort('updated')}>Updated</Th>
                {actions.canEdit && <Th className="w-[170px]"><span className="sr-only">Actions</span></Th>}
              </Tr>
            </THead>
            <TBody>
              {items.map((inc) => (
                <Tr key={inc.id} className="hover:bg-hover">
                  <Td><SeverityIndicator severity={inc.severity} status={inc.status} /></Td>
                  <Td className="max-w-0 py-2">
                    <Link href={`/incidents/${inc.id}`} className="block truncate font-medium text-fg hover:text-accent">
                      {inc.title}
                    </Link>
                    <p className="truncate text-meta text-fg-3">
                      <span title={inc.rule}>{ruleLabel(inc.rule)}</span>
                      {inc.summary && <> · {inc.summary}</>}
                    </p>
                  </Td>
                  <Td>
                    <IncidentStatusBadge status={inc.status} />
                    {inc.status === 'acknowledged' && inc.acknowledged_by && (
                      <p className="mt-0.5 truncate text-micro text-fg-3">by {inc.acknowledged_by}</p>
                    )}
                  </Td>
                  <Td className="py-1.5"><AffectedHosts incident={inc} /></Td>
                  <Td numeric><Age inc={inc} now={now} /></Td>
                  <Td numeric muted><span title={formatDateTime(inc.updated_at)}>{formatAgo(inc.updated_at, now)}</span></Td>
                  {actions.canEdit && <Td><Actions inc={inc} actions={actions} /></Td>}
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableContainer>
      </Card>

      <ul className="space-y-2 min-[760px]:hidden" aria-label="Incidents">
        {items.map((inc) => (
          <li key={inc.id}>
            <Card padding="sm" glow={incidentNeedsGlow(inc) ? 'crit' : undefined}>
              <div className="flex items-center justify-between gap-2">
                <SeverityIndicator severity={inc.severity} status={inc.status} />
                <IncidentStatusBadge status={inc.status} />
              </div>
              <Link href={`/incidents/${inc.id}`} className="mt-2 block font-medium text-fg hover:text-accent [overflow-wrap:anywhere]">
                {inc.title}
              </Link>
              <p className="mt-0.5 truncate text-meta text-fg-3">
                <span title={inc.rule}>{ruleLabel(inc.rule)}</span>
                {' · '}<Age inc={inc} now={now} />
              </p>
              {inc.summary && <p className="mt-1 line-clamp-2 text-meta text-fg-2">{inc.summary}</p>}
              <div className="mt-2"><AffectedHosts incident={inc} max={3} /></div>
              {actions.canEdit && inc.status !== 'resolved' && (
                <div className="mt-3 border-t border-border pt-3"><Actions inc={inc} actions={actions} compact /></div>
              )}
            </Card>
          </li>
        ))}
      </ul>
    </>
  );
}
