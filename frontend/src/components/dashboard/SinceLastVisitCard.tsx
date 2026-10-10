'use client';

import Link from 'next/link';
import { EmptyState } from '@/components/ui/EmptyState';
import { changeTiles, formatClock, formatWhen } from '@/lib/dashboard';
import type { SectionError, SinceLastVisitSection } from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';

export function changesHref(since: string | null | undefined, types?: string): string {
  const qs = new URLSearchParams();
  if (since) qs.set('since', since);
  if (types) qs.set('types', types);
  const s = qs.toString();
  return s ? `/changes?${s}` : '/changes';
}

/** Q4 "What changed since my last visit?" */
export function SinceLastVisitCard({ slv, loading, error, generatedAt, className }: {
  slv: SinceLastVisitSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  generatedAt: string | null | undefined;
  className?: string;
}) {
  const { now } = useDashboardCtx();
  const { tiles, rest } = slv ? changeTiles(slv.counts, slv.items) : { tiles: [], rest: 0 };
  const range = slv
    ? slv.fallback
      ? 'Last 24 h · no earlier visit stored'
      : `${formatWhen(slv.since, now)} – ${formatClock(generatedAt)}`
    : undefined;

  return (
    <DashboardCard
      id="dash-since"
      title="Since your last visit"
      meta={range}
      loading={loading}
      error={error}
      className={className}
      actions={slv && <Link href={changesHref(slv.since)} className="text-ui font-medium text-accent hover:text-accent-hover">All changes</Link>}
    >
      {slv && (slv.total === 0 ? (
        <EmptyState compact variant="confirmed" title="Nothing changed" description="No incidents, hosts, agents or maintenance changes in this window." asOf={formatClock(generatedAt)} />
      ) : (
        <>
          <ul className="grid grid-cols-5 gap-y-[18px] max-[1199px]:grid-cols-3 max-[759px]:grid-cols-2">
            {tiles.map((tile) => (
              <li
                key={tile.type}
                className={
                  'min-w-0 border-l border-border px-5 py-0.5 first:border-l-0 first:pl-0 ' +
                  'max-[1199px]:[&:nth-child(4)]:border-l-0 max-[1199px]:[&:nth-child(4)]:pl-0 ' +
                  'max-[759px]:border-l-0 max-[759px]:pl-0 max-[759px]:pr-2'
                }
              >
                <Link href={changesHref(slv.since, tile.type)} className="group block">
                  <span className="num block font-display text-num-sm font-medium leading-[1.05] tracking-[-0.045em] text-fg">{tile.count}</span>
                  <span className="mt-1.5 block text-ui font-medium text-fg group-hover:text-accent">{tile.label}</span>
                  {tile.detail && <span className="mt-0.5 line-clamp-2 block text-meta text-fg-2">{tile.detail}</span>}
                </Link>
              </li>
            ))}
          </ul>
          {rest > 0 && (
            <p className="mt-3 text-meta text-fg-3">
              + {rest} more change{rest === 1 ? '' : 's'} of other types ·{' '}
              <Link href={changesHref(slv.since)} className="text-accent hover:text-accent-hover">see all</Link>
            </p>
          )}
        </>
      ))}
    </DashboardCard>
  );
}
