'use client';

import Link from 'next/link';
import { HardDrive, Hourglass, ShieldCheck, Wrench } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';
import { formatClock, upcomingView } from '@/lib/dashboard';
import type { SectionError, UpcomingSection } from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';

const ICONS = { maintenance: Wrench, certificate: ShieldCheck, disk: HardDrive } as const;
const MAX_ROWS = 5;

/** Q7 / "what is planned": maintenance, expiring certificates, disks filling up. */
export function UpcomingCard({ upcoming, loading, error, generatedAt, className }: {
  upcoming: UpcomingSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  generatedAt: string | null | undefined;
  className?: string;
}) {
  const { now } = useDashboardCtx();
  const items = upcoming?.items ?? [];
  const rest = items.length - MAX_ROWS;

  return (
    <DashboardCard
      id="dash-upcoming"
      title="Upcoming"
      meta="24 h maintenance · 30 d forecasts"
      loading={loading}
      error={error}
      className={className}
    >
      {upcoming && (
        <>
          {items.length === 0 ? (
            upcoming.agent_disk_predictions_ready ? (
              <EmptyState compact variant="confirmed" title="Nothing due" description="No maintenance in the next 24 h, no certificate or disk due within 30 days." asOf={formatClock(generatedAt)} />
            ) : (
              <EmptyState compact title="Nothing due yet" description="No maintenance or certificate is due. Disk forecasts are still warming up." />
            )
          ) : (
            <ul className="[&>li:first-child>*]:pt-0">
              {items.slice(0, MAX_ROWS).map((it, i) => {
                const v = upcomingView(it, now);
                const Icon = ICONS[v.icon];
                const body = (
                  <>
                    <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-ctl bg-surface-2 text-fg-2">
                      <Icon size={16} />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-ui font-medium text-fg group-hover:text-accent">{v.title}</span>
                      <span className="block truncate text-meta text-fg-2">{v.subtitle}</span>
                      {v.progressPct !== undefined && (
                        <span aria-hidden="true" className="mt-1.5 block h-1 max-w-[180px] overflow-hidden rounded-[2px] bg-surface-3">
                          <i className="block h-full bg-warning" style={{ width: `${Math.min(100, Math.max(0, v.progressPct))}%` }} />
                        </span>
                      )}
                    </span>
                    <span className="num whitespace-nowrap text-right text-ui font-medium text-fg">
                      {v.when}
                      <small className="block text-micro font-normal text-fg-3">{v.whenDetail}</small>
                    </span>
                  </>
                );
                const cls = 'grid grid-cols-[32px_1fr_auto] items-center gap-3 py-2.5';
                return (
                  <li key={`${it.kind}-${i}`} className="border-b border-border last:border-b-0">
                    {v.href ? <Link href={v.href} className={`${cls} group`}>{body}</Link> : <div className={cls}>{body}</div>}
                  </li>
                );
              })}
            </ul>
          )}
          {rest > 0 && <p className="pt-2 text-meta text-fg-3">+ {rest} more</p>}
          {!upcoming.agent_disk_predictions_ready && items.length > 0 && (
            <p className="mt-auto flex items-start gap-2 rounded-ctl border border-dashed border-border-2 p-2.5 text-meta text-fg-2">
              <Hourglass size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
              Agent disk predictions are warming up — agent disks are not forecast yet.
            </p>
          )}
          {!upcoming.agent_disk_predictions_ready && items.length === 0 && (
            <p className="mt-auto flex items-start gap-2 rounded-ctl border border-dashed border-border-2 p-2.5 text-meta text-fg-2">
              <Hourglass size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
              Predictions warming up: forecasts appear after the first scheduler run.
            </p>
          )}
        </>
      )}
    </DashboardCard>
  );
}
