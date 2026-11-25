'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Award, HardDrive, Plug, Radio, ScanSearch, Server, Wrench } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';
import { buttonClasses } from '@/components/ui/Button';
import { DashboardCard, useDashboardCtx } from '@/components/dashboard/DashboardCard';
import { formatClock, upcomingView } from '@/lib/dashboard';
import type { NeedIcon, NeedItem, NeedTone } from '@/lib/overview';
import { cn } from '@/lib/utils';
import type { MaintenanceUpcoming, SectionError, UpcomingSection } from '@/types/dashboard';

const CAP = 6;

const ICON: Record<NeedIcon, typeof Server> = {
  probe: Radio, host: Server, integration: Plug, disk: HardDrive, certificate: Award, discovery: ScanSearch,
};

const TONE: Record<NeedTone, string> = {
  down: 'bg-down-soft text-down',
  warning: 'bg-warning-soft text-warning',
  unknown: 'ng-hatch text-fg-2',
  info: 'bg-accent-soft text-accent',
};

function Row({ item }: { item: NeedItem }) {
  const Icon = ICON[item.icon];
  return (
    <li className="flex items-center gap-3 rounded-tile bg-surface-2 p-2.5">
      <span aria-hidden="true" className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-pill', TONE[item.tone])}>
        <Icon size={16} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-ui font-medium leading-snug text-fg">{item.title}</span>
        <span className="block truncate text-meta text-fg-2">{item.detail}</span>
      </span>
      <Link href={item.href} className={cn(buttonClasses({ size: 'sm' }), 'shrink-0 border-transparent bg-tip text-tip-fg hover:bg-tip/90')}>
        {item.action}
        <span className="sr-only">: {item.title}</span>
      </Link>
    </li>
  );
}

/**
 * Everything that wants attention besides the open incidents, in one list
 * (concept F). Folded per cause and aggregated, at most six rows.
 */
export function NeedsLook({ items, loading, error, generatedAt, className }: {
  items: NeedItem[];
  loading: boolean;
  error: SectionError | null;
  generatedAt: string | null | undefined;
  className?: string;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, CAP);
  return (
    <DashboardCard
      id="dash-needs"
      title="Needs a look"
      meta={items.length ? `${items.length} thing${items.length === 1 ? '' : 's'} besides the open incidents` : undefined}
      loading={loading}
      error={error}
      className={className}
    >
      {items.length === 0 ? (
        <EmptyState compact variant="confirmed" title="Nothing else needs a look" asOf={formatClock(generatedAt)} />
      ) : (
        <>
          <ul className="flex flex-col gap-1.5">{shown.map((i) => <Row key={i.key} item={i} />)}</ul>
          {items.length > CAP && (
            <button type="button" onClick={() => setAll((v) => !v)} className="mx-auto mt-2.5 text-meta font-medium text-accent hover:text-accent-hover">
              {all ? 'Show fewer' : `Show all ${items.length}`}
            </button>
          )}
        </>
      )}
    </DashboardCard>
  );
}

/** Maintenance windows active now or starting in the next 24 h. */
export function ComingUp({ upcoming, loading, error, className }: {
  upcoming: UpcomingSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const { now } = useDashboardCtx();
  const windows = (upcoming?.items ?? []).filter((i): i is MaintenanceUpcoming => i.kind === 'maintenance');
  return (
    <DashboardCard
      id="dash-coming"
      title="Coming up"
      meta="Maintenance windows"
      loading={loading}
      error={error}
      className={className}
      actions={<Link href="/alerts?tab=maintenance" className="text-ui font-medium text-accent hover:text-accent-hover">Calendar</Link>}
    >
      {windows.length === 0 ? (
        <p className="text-meta text-fg-3">No maintenance in the next 24 hours.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {windows.map((w, i) => {
            const v = upcomingView(w, now);
            const start = w.starts_at ? Date.parse(w.starts_at) : NaN;
            const end = w.ends_at ? Date.parse(w.ends_at) : NaN;
            const pct = w.phase === 'active' && Number.isFinite(start) && Number.isFinite(end) && end > start
              ? Math.min(100, Math.max(0, ((now - start) / (end - start)) * 100)) : null;
            return (
              <li key={`${w.object.id ?? i}-${w.starts_at}`} className="flex items-center gap-3 rounded-tile bg-surface-2 p-2.5">
                <span aria-hidden="true" className="grid h-8 w-8 shrink-0 place-items-center rounded-pill bg-maint-soft text-maint">
                  <Wrench size={16} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ui font-medium text-fg">{v.title}</span>
                  <span className="block truncate text-meta text-fg-2">{v.subtitle}</span>
                </span>
                <span className="text-right">
                  <span className="num block font-display text-body font-medium text-fg">{v.when}</span>
                  {pct !== null ? (
                    <span role="progressbar" aria-label="Window progress" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} className="mt-1 block h-[5px] w-[70px] overflow-hidden rounded-pill bg-surface-3">
                      <i className="block h-full rounded-pill bg-accent" style={{ width: `${pct}%` }} />
                    </span>
                  ) : (
                    <span className="block text-micro text-fg-3">{v.whenDetail}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </DashboardCard>
  );
}

