'use client';

import Link from 'next/link';
import { useId, useState } from 'react';
import { ArrowRight, Check, ChevronDown } from 'lucide-react';
import { Button, buttonClasses } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusDot } from '@/components/ui/StatusDot';
import { SeverityIcon } from '@/components/dashboard/IncidentsCard';
import { DashboardCard, useDashboardCtx } from '@/components/dashboard/DashboardCard';
import { useIncidentAction } from '@/hooks/queries/useAlerts';
import { useIncidentLatency } from '@/hooks/queries/useDashboard';
import { describeStates, formatClock, formatDuration, hostState, incidentHostsLabel, severityLabel } from '@/lib/dashboard';
import { incidentCause, type IncidentCause } from '@/lib/overview';
import { cn } from '@/lib/utils';
import type { DashboardIncident, IncidentsSection, LatencySection, SectionError, TopologySection } from '@/types/dashboard';
import { LatencyChart } from './LatencyChart';

const MAX_ROWS = 5;

/** Correlation label pill (concept F). Only labels the page can derive honestly are used. */
function RelationTag({ kind }: { kind: 'confirmed' | 'rule' }) {
  return (
    <span
      className={cn(
        'inline-flex h-[20px] shrink-0 items-center rounded-pill px-2 text-micro font-medium',
        kind === 'confirmed' ? 'bg-tip text-tip-fg' : 'bg-accent-btn text-on-accent',
      )}
    >
      {kind === 'confirmed' ? 'Confirmed' : 'Rule-based'}
    </span>
  );
}

/** Edge of the network → the parent the hosts share → the hosts, by state. */
function CausePath({ cause }: { cause: IncidentCause }) {
  const label = `Path: ${cause.chain.map((n) => n.name).join(' to ')}, then ${describeStates(cause.byState)}`;
  return (
    <div role="group" aria-label={label} className="mb-3 flex flex-wrap items-center gap-y-1.5">
      {cause.chain.map((n, i) => {
        const last = i === cause.chain.length - 1;
        return (
          <span key={n.id} className="flex items-center">
            {i > 0 && <span aria-hidden="true" className={cn('h-[3px] w-5 rounded-pill', last ? 'bg-down' : 'bg-accent')} />}
            <span
              aria-hidden="true"
              className={cn(
                'inline-flex h-8 items-center gap-2 rounded-pill bg-surface pl-2 pr-3 font-mono text-meta shadow-card',
                last && 'ring-[1.5px] ring-degraded',
              )}
            >
              <StatusDot status={hostState(n.state)} size="sm" glow={false} />
              {n.name}
            </span>
          </span>
        );
      })}
      <span aria-hidden="true" className="h-[3px] w-5 rounded-pill bg-down" />
      <span aria-hidden="true" className="flex flex-wrap gap-1.5">
        {(['down', 'warning', 'degraded', 'unknown'] as const).filter((s) => cause.byState[s]).map((s) => (
          <span
            key={s}
            className={cn(
              'inline-flex h-8 items-center rounded-pill px-3 text-meta font-semibold',
              s === 'down' && 'bg-down-soft text-down',
              s === 'warning' && 'bg-warning-soft text-warning',
              s === 'degraded' && 'bg-degraded-soft text-degraded',
              s === 'unknown' && 'bg-unknown-soft text-fg-2',
            )}
          >
            {cause.byState[s]} {s === 'unknown' ? 'no data' : s}
          </span>
        ))}
      </span>
      <Link href="/topology" className="ml-3 text-meta font-medium text-accent hover:text-accent-hover">Show in topology</Link>
    </div>
  );
}

function IncidentItem({ inc, topology, latency, defaultOpen }: {
  inc: DashboardIncident;
  topology: TopologySection | null | undefined;
  latency: LatencySection | null | undefined;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const { open: openPanel } = useDashboardCtx();
  const act = useIncidentAction();
  const cause = incidentCause(inc, topology);
  // The dashboard brings the chart of the worst incident; any other one is
  // loaded when it is opened.
  const own = latency && latency.incident_id === inc.id ? latency : null;
  const fetched = useIncidentLatency(open && !own ? inc.id : null);
  const chart = own ?? fetched.data ?? null;
  const acked = inc.acknowledged || (act.isSuccess && act.variables?.action === 'acknowledge');

  return (
    <li className="rounded-[20px] bg-surface-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 rounded-[20px] p-3 text-left"
      >
        <SeverityIcon severity={inc.severity} acknowledged={acked} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body font-medium text-fg">#{inc.id} {inc.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-meta text-fg-2">
            <span>{severityLabel(inc.severity)} · {incidentHostsLabel(inc.host_count)}{acked ? ' · acknowledged' : ''}</span>
            {cause && <span className="max-[759px]:hidden"><RelationTag kind="confirmed" /></span>}
            <span className="max-[759px]:hidden"><RelationTag kind="rule" /></span>
          </span>
        </span>
        <span className="num whitespace-nowrap text-meta text-fg-2 max-[759px]:hidden">{formatDuration(inc.age_seconds)}</span>
        <ChevronDown size={18} aria-hidden="true" className={cn('shrink-0 text-fg-3 transition-transform', open && 'rotate-180')} />
      </button>
      <div id={bodyId} hidden={!open} className="px-3.5 pb-3.5 pl-[58px] max-[759px]:pl-3.5">
        {cause && <CausePath cause={cause} />}
        <dl className="mb-3 grid gap-1.5 text-meta">
          {cause && (
            <div className="flex flex-wrap items-baseline gap-2.5">
              <dt className="w-[92px] shrink-0"><RelationTag kind="confirmed" /></dt>
              <dd className="text-fg-2">
                {cause.shared} of {inc.host_count ?? cause.shared} hosts share the parent <b className="font-medium text-fg">{cause.chain[cause.chain.length - 1]?.name}</b> in the topology.
              </dd>
            </div>
          )}
          <div className="flex flex-wrap items-baseline gap-2.5">
            <dt className="w-[92px] shrink-0"><RelationTag kind="rule" /></dt>
            <dd className="text-fg-2">
              Rule <span className="font-mono text-fg">{inc.rule}</span>{inc.summary ? ` · ${inc.summary}` : ''}
            </dd>
          </div>
        </dl>
        {chart && <div className="mb-3"><LatencyChart latency={chart} /></div>}
        <div className="flex flex-wrap gap-2">
          <Link href={`/incidents/${inc.id}`} className={buttonClasses({ variant: 'primary', size: 'sm' })}>
            Open incident <ArrowRight size={14} aria-hidden="true" />
          </Link>
          <Button
            variant="secondary"
            size="sm"
            disabled={acked || act.isPending}
            loading={act.isPending}
            onClick={() => act.mutate({ id: inc.id, action: 'acknowledge' })}
          >
            {acked ? <><Check size={14} aria-hidden="true" /> Acknowledged</> : 'Acknowledge'}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => openPanel({ kind: 'incident', id: inc.id })}>
            Affected hosts
          </Button>
        </div>
      </div>
    </li>
  );
}

/**
 * Open incidents, worst first, one line each. Opening one shows the path to
 * the shared parent, the evidence with its label, the metric of its hosts and
 * the actions. Labels are only the ones the page can derive: "Confirmed" from
 * topology parent links, "Rule-based" from the rule that fired. "Suspected"
 * needs backend support (concept F, phase 3).
 */
export function OpenIncidents({ incidents, topology, latency, loading, error, generatedAt, className }: {
  incidents: IncidentsSection | null | undefined;
  topology: TopologySection | null | undefined;
  latency: LatencySection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  generatedAt: string | null | undefined;
  className?: string;
}) {
  const items = incidents?.items ?? [];
  const rest = items.length - MAX_ROWS;
  const resolved = incidents?.resolved_today ?? [];

  return (
    <DashboardCard
      id="dash-incidents"
      title="Open incidents"
      meta={items.length ? 'Click one for its cause path, evidence and actions' : undefined}
      loading={loading}
      error={error}
      className={className}
      actions={<Link href="/alerts" className="text-ui font-medium text-accent hover:text-accent-hover">All incidents</Link>}
    >
      {incidents && (
        <>
          {items.length === 0 ? (
            <EmptyState compact variant="confirmed" title="No open incidents" asOf={formatClock(generatedAt)} />
          ) : (
            <ul className="flex flex-col gap-2">
              {items.slice(0, MAX_ROWS).map((inc) => (
                <IncidentItem key={inc.id} inc={inc} topology={topology} latency={latency} defaultOpen={false} />
              ))}
            </ul>
          )}
          {rest > 0 && (
            <Link href="/alerts" className="mt-2 text-meta text-fg-2 hover:text-accent">+ {rest} more open</Link>
          )}
          {resolved.length > 0 && (
            <p className="mt-3 flex items-center gap-2 text-meta text-fg-2">
              <Check size={14} className="shrink-0 text-ok" aria-hidden="true" />
              <span className="min-w-0">
                Resolved today:{' '}
                {resolved.slice(0, 2).map((r, i) => (
                  <span key={r.id}>
                    {i > 0 && ' · '}
                    <Link href={`/incidents/${r.id}`} className="hover:text-accent"><b className="font-medium text-fg">#{r.id}</b> {r.title}</Link>
                  </span>
                ))}
                {resolved.length > 2 && ` · +${resolved.length - 2} more`}
              </span>
            </p>
          )}
        </>
      )}
    </DashboardCard>
  );
}
