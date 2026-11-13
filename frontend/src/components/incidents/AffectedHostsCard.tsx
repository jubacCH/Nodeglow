'use client';

import Link from 'next/link';
import { ArrowUpRight, HelpCircle, Server } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusDot } from '@/components/ui/StatusDot';
import { STATE_TEXT } from '@/lib/status';
import { cn } from '@/lib/utils';
import { hostHealth, hostLabel, hostStateLabel, type IncidentHost, type IncidentItem } from '@/lib/incidents';

function HostRow({ host }: { host: IncidentHost }) {
  const deleted = host.name === null && host.hostname === null;
  const health = hostHealth(host.state);
  const tone = health === 'disabled' ? 'text-fg-3' : STATE_TEXT[health];
  return (
    <li className="flex items-start gap-2.5 py-2.5">
      <span className="mt-[5px]"><StatusDot status={health} glow={false} label="" /></span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          {deleted ? (
            <span className="truncate font-medium text-fg-2">{hostLabel(host)}</span>
          ) : (
            <Link prefetch={false} href={`/hosts/${host.id}`} className="truncate font-medium text-fg hover:text-accent">{hostLabel(host)}</Link>
          )}
          <span className={cn('shrink-0 text-meta font-medium', deleted ? 'text-fg-3' : tone)}>
            {deleted ? 'Deleted' : hostStateLabel(host.state)}
          </span>
        </div>
        {host.hostname && host.hostname !== host.name && <p className="truncate font-mono text-meta text-fg-3">{host.hostname}</p>}
        {host.state_reason && !deleted && <p className="mt-0.5 text-meta text-fg-2 wrap-anywhere">{host.state_reason}</p>}
        {/* The name is a link too, but nothing shows it: say where it goes. */}
        {!deleted && (
          <Link
            prefetch={false}
            href={`/hosts/${host.id}`}
            className="mt-1 inline-flex items-center gap-1 text-meta font-medium text-accent hover:text-accent-hover"
            aria-label={`Open host ${hostLabel(host)}`}
          >
            Open host <ArrowUpRight size={13} aria-hidden="true" />
          </Link>
        )}
      </div>
    </li>
  );
}

/**
 * Hosts the incident affects, with their *current* state. `host_ids` null =
 * the backend did not record hosts (incidents before host tracking, rules
 * without a host) — that is "unknown", not "no hosts".
 */
export function AffectedHostsCard({ incident }: { incident: IncidentItem }) {
  const ids = incident.host_ids;
  const hosts = incident.hosts ?? [];
  const count = incident.host_count ?? ids?.length ?? null;
  return (
    <Card as="section" aria-labelledby="inc-hosts">
      <CardHeader title="Affected hosts" titleId="inc-hosts" meta={count !== null ? <span className="num">{count}</span> : undefined} />
      {incident.integration && (
        <Link
          prefetch={false}
          href={`/integration/${incident.integration.type}/${incident.integration.id}`}
          className="mb-3 flex items-center justify-between gap-2 rounded-ctl border border-border bg-surface-2 px-3 py-2 text-ui hover:border-border-2"
        >
          <span className="min-w-0">
            <span className="block text-meta text-fg-3">Integration</span>
            <span className="block truncate font-medium text-fg">{incident.integration.name}</span>
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 text-meta font-medium text-accent">
            Open integration <ArrowUpRight size={13} aria-hidden="true" />
          </span>
        </Link>
      )}
      {ids === null || ids === undefined ? (
        <EmptyState
          compact
          icon={HelpCircle}
          title="Not recorded"
          description="This incident does not say which hosts it affects — it was opened before host tracking, or its rule has no host reference."
        />
      ) : ids.length === 0 ? (
        <EmptyState compact icon={Server} title="No hosts affected" description="Recorded without a host, e.g. a log spike or a self-check." />
      ) : (
        <>
          <ul className="-my-2.5 divide-y divide-border">
            {hosts.map((h) => <HostRow key={h.id} host={h} />)}
          </ul>
          {count !== null && count > hosts.length && (
            <p className="mt-3 text-meta text-fg-3">and {count - hosts.length} more not shown</p>
          )}
        </>
      )}
    </Card>
  );
}
