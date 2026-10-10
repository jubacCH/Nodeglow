'use client';

import Link from 'next/link';
import { Check, Info, TriangleAlert } from 'lucide-react';
import { BigNumber } from '@/components/ui/BigNumber';
import { EmptyState } from '@/components/ui/EmptyState';
import {
  describeIncidentDays, formatClock, formatDuration, incidentDayBars, incidentHostsLabel,
  severityLabel, severityTone,
} from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { DashboardIncident, IncidentsSection, SectionError } from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';

/** Severity tile of an incident; the glow sits here, on the object (E3). */
export function SeverityIcon({ severity, acknowledged, size = 'md' }: { severity: string; acknowledged?: boolean; size?: 'md' | 'lg' }) {
  const tone = severityTone(severity);
  const Icon = tone === 'info' ? Info : TriangleAlert;
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid shrink-0 place-items-center',
        size === 'md' ? 'h-[30px] w-[30px] rounded-ctl' : 'h-11 w-11 rounded-card',
        tone === 'crit' && 'bg-down-soft text-down ng-glow-crit',
        tone === 'warn' && 'bg-warning-soft text-warning ng-glow-warn',
        tone === 'info' && 'bg-surface-2 text-fg-2',
        tone !== 'info' && acknowledged && 'ng-glow-acked',
      )}
    >
      <Icon size={size === 'md' ? 16 : 22} />
    </span>
  );
}

export function IncidentRow({ inc, onOpen }: { inc: DashboardIncident; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="-mx-2.5 grid w-[calc(100%+20px)] grid-cols-[auto_1fr_auto] items-center gap-3 rounded-ctl p-2.5 text-left hover:bg-surface-2"
    >
      <SeverityIcon severity={inc.severity} acknowledged={inc.acknowledged} />
      <span className="min-w-0">
        <span className="block truncate text-ui font-medium text-fg">{inc.title}</span>
        <span className="block truncate text-meta text-fg-2">
          #{inc.id} · {severityLabel(inc.severity)} · {incidentHostsLabel(inc.host_count)}
        </span>
      </span>
      <span className="whitespace-nowrap text-right text-meta text-fg-2">
        <span className="num">{formatDuration(inc.age_seconds)}</span>
        {inc.acknowledged && <span className="block text-micro text-accent">Acknowledged</span>}
      </span>
    </button>
  );
}

const MAX_ROWS = 4;

/** Q2/Q5 — open and acknowledged incidents, worst first. */
export function IncidentsCard({ incidents, loading, error, generatedAt, className }: {
  incidents: IncidentsSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  generatedAt: string | null | undefined;
  className?: string;
}) {
  const { open } = useDashboardCtx();
  const c = incidents?.counts;
  const bars = incidents ? incidentDayBars(incidents.per_day) : [];
  const rest = (incidents?.items.length ?? 0) - MAX_ROWS;
  const breakdown = c
    ? [c.critical && `${c.critical} critical`, c.warning && `${c.warning} warning`, c.info && `${c.info} info`].filter(Boolean).join(' · ')
    : '';

  return (
    <DashboardCard
      id="dash-incidents"
      title="Incidents"
      loading={loading}
      error={error}
      className={className}
      actions={<Link href="/alerts" className="text-ui font-medium text-accent hover:text-accent-hover">View all</Link>}
    >
      {incidents && c && (
        <>
          <div className="mb-3.5 flex items-end justify-between gap-3">
            <BigNumber
              value={c.open}
              unit="open"
              label={c.open ? `${breakdown}${c.acknowledged ? ` · ${c.acknowledged} acknowledged` : ''}` : 'Nothing open'}
            />
            {bars.length > 0 && (
              <div role="img" aria-label={describeIncidentDays(incidents.per_day)} className="flex h-9 items-end gap-[3px]">
                {bars.map((b) => (
                  <span
                    key={b.date}
                    title={b.label}
                    className={cn('w-[7px] min-h-[2px] rounded-[2px]', b.today ? 'bg-accent' : 'bg-surface-3')}
                    style={{ height: `${b.heightPct}%` }}
                  />
                ))}
              </div>
            )}
          </div>

          {incidents.items.length === 0 ? (
            <EmptyState compact variant="confirmed" title="No open incidents" asOf={formatClock(generatedAt)} />
          ) : (
            <div className="flex flex-col gap-0.5">
              {incidents.items.slice(0, MAX_ROWS).map((inc) => (
                <IncidentRow key={inc.id} inc={inc} onOpen={() => open({ kind: 'incident', id: inc.id })} />
              ))}
              {rest > 0 && (
                <Link href="/alerts" className="px-0 py-1.5 text-meta text-fg-2 hover:text-accent">
                  + {rest} more open
                </Link>
              )}
            </div>
          )}

          <div className="mt-auto border-t border-border pt-3.5">
            <div className="mb-1.5 text-meta text-fg-3">Resolved today</div>
            {incidents.resolved_today.length === 0 ? (
              <p className="text-meta text-fg-3">None so far</p>
            ) : (
              <ul>
                {incidents.resolved_today.slice(0, 3).map((r) => (
                  <li key={r.id}>
                    <Link href={`/incidents/${r.id}`} className="grid grid-cols-[18px_1fr_auto] items-center gap-2 py-1 text-ui text-fg hover:text-accent">
                      <Check size={13} className="text-ok" aria-hidden="true" />
                      <span className="truncate">#{r.id} {r.title}</span>
                      <span className="num text-meta text-fg-3">
                        {formatClock(r.opened_at)}–{formatClock(r.resolved_at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </DashboardCard>
  );
}
