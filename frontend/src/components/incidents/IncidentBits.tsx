'use client';

import Link from 'next/link';
import { CheckCircle2, CircleDot, Eye } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { cn } from '@/lib/utils';
import {
  SEVERITY_LABEL, STATUS_LABEL, hostHealth, hostLabel, hostStateLabel, incidentNeedsGlow, severityState,
  type IncidentHost, type IncidentItem, type IncidentSeverity, type IncidentStatus,
} from '@/lib/incidents';

/** Severity as dot + text. Only a critical, unacknowledged incident glows
 *  (and breathes); acknowledged or resolved ones stay calm. */
export function SeverityIndicator({ severity, status, size = 'md', glow = true, className }: {
  severity: IncidentSeverity;
  status: IncidentStatus;
  size?: 'sm' | 'md' | 'lg';
  /** Off where another element on screen already carries the glow. */
  glow?: boolean;
  className?: string;
}) {
  const state = severityState(severity);
  const live = glow && incidentNeedsGlow({ severity, status });
  const text = severity === 'critical' ? 'text-down' : severity === 'warning' ? 'text-warning' : 'text-fg-2';
  return (
    <span className={cn('inline-flex items-center gap-2 whitespace-nowrap font-medium', text, status === 'resolved' && 'opacity-80', className)}>
      {state ? (
        <StatusDot status={state} size={size} glow={live} breathe={live} label="" />
      ) : (
        <span aria-hidden="true" className={cn('shrink-0 rounded-full bg-fg-3', size === 'lg' ? 'h-[10px] w-[10px]' : size === 'sm' ? 'h-[6px] w-[6px]' : 'h-[8px] w-[8px]')} />
      )}
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

const STATUS_ICON = { open: CircleDot, acknowledged: Eye, resolved: CheckCircle2 } as const;

/** Workflow status of an incident (not a health state): icon + text. */
export function IncidentStatusBadge({ status, className }: { status: IncidentStatus; className?: string }) {
  const Icon = STATUS_ICON[status];
  return (
    <Badge tone={status === 'resolved' ? 'ok' : 'neutral'} className={cn(status === 'open' && 'text-fg', className)}>
      <Icon size={11} aria-hidden="true" />
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/** One host with its current state as a small chip, linking to the host. */
export function HostChip({ host }: { host: IncidentHost }) {
  const deleted = host.name === null && host.hostname === null;
  const label = hostLabel(host);
  const stateText = hostStateLabel(host.state);
  const title = [stateText, host.state_reason].filter(Boolean).join(' · ');
  const inner = (
    <>
      <StatusDot status={hostHealth(host.state)} size="sm" glow={false} label="" />
      <span className="truncate">{label}</span>
      <span className="sr-only">: {deleted ? 'host deleted' : stateText}</span>
    </>
  );
  const cls = 'inline-flex h-[22px] max-w-[180px] items-center gap-1.5 rounded-chip border border-border-2 bg-surface-2 px-1.5 text-micro font-medium text-fg-2';
  if (deleted) return <span className={cn(cls, 'border-dashed')} title="Host deleted">{inner}</span>;
  return (
    <Link prefetch={false} href={`/hosts/${host.id}`} title={title} className={cn(cls, 'hover:border-line hover:text-fg')} onClick={(e) => e.stopPropagation()}>
      {inner}
    </Link>
  );
}

/**
 * Affected hosts of a list row: count and up to `max` chips. `host_ids` null
 * means the backend did not record hosts for this incident — shown as
 * "Not recorded", never as "no hosts".
 */
export function AffectedHosts({ incident, max = 2 }: { incident: IncidentItem; max?: number }) {
  const count = incident.host_count;
  if (count === null || count === undefined || incident.host_ids === null || incident.host_ids === undefined) {
    return (
      <span className="text-meta text-fg-3" title="Hosts were not recorded for this incident (older incident or a rule without a host).">
        Not recorded
      </span>
    );
  }
  if (count === 0) return <span className="text-meta text-fg-3">No hosts</span>;
  const hosts = incident.hosts ?? [];
  const shown = hosts.slice(0, max);
  const rest = count - shown.length;
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-1">
      {shown.map((h) => <HostChip key={h.id} host={h} />)}
      {rest > 0 && (
        <span className="num text-micro font-medium text-fg-3" aria-label={`and ${rest} more host${rest === 1 ? '' : 's'}`}>
          +{rest}
        </span>
      )}
    </span>
  );
}
