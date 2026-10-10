'use client';

import { createContext, useContext, useId, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { WidgetErrorBoundary } from '@/components/ui/WidgetErrorBoundary';
import { cn } from '@/lib/utils';
import type { HostGroup, SectionError, TopoNode } from '@/types/dashboard';

/** What a card can open in the side panel. */
export type PanelTarget =
  | { kind: 'incident'; id: number }
  | { kind: 'host'; id: number; hint?: Partial<TopoNode> }
  | { kind: 'group'; group: HostGroup };

export interface DashboardCtx {
  open: (target: PanelTarget) => void;
  /** ms clock, ticks so ages ("11 min ago") stay current between fetches. */
  now: number;
}

const Ctx = createContext<DashboardCtx>({ open: () => undefined, now: 0 });
export const DashboardProvider = Ctx.Provider;
export const useDashboardCtx = () => useContext(Ctx);

interface DashboardCardProps {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  /** First load in flight: skeleton in the card's shape. */
  loading?: boolean;
  /** This section failed (dashboard `errors[]`); the rest of the page renders. */
  error?: SectionError | null;
  glow?: 'crit' | 'warn';
  acknowledged?: boolean;
  className?: string;
  id?: string;
  skeleton?: ReactNode;
  children?: ReactNode;
}

/**
 * One dashboard card: a labelled <section> with the E3 header, a skeleton
 * while loading and a card-level error state when its section failed — so a
 * broken source never blanks the page (IA §6.5 "partial failure").
 */
export function DashboardCard({
  title, meta, actions, loading, error, glow, acknowledged, className, id, skeleton, children,
}: DashboardCardProps) {
  const titleId = useId();
  return (
    <Card
      as="section"
      id={id}
      aria-labelledby={titleId}
      aria-busy={loading || undefined}
      glow={!loading && !error ? glow : undefined}
      acknowledged={acknowledged}
      className={cn('flex scroll-mt-[76px] flex-col', className)}
    >
      <CardHeader title={title} titleId={titleId} actions={actions} meta={meta} />
      {loading ? (
        skeleton ?? (
          <div className="space-y-2.5" aria-label="Loading">
            <Skeleton className="h-9 w-1/2" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-4/5" />
          </div>
        )
      ) : error ? (
        <SectionErrorState error={error} />
      ) : (
        <WidgetErrorBoundary label={typeof title === 'string' ? title : 'card'}>
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        </WidgetErrorBoundary>
      )}
    </Card>
  );
}

export function SectionErrorState({ error }: { error: SectionError }) {
  return (
    <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-1.5 px-4 py-6 text-center">
      <AlertTriangle size={20} className="text-warning" aria-hidden="true" />
      <p className="text-ui font-medium text-fg">This section could not be loaded</p>
      <p className="max-w-xs text-meta text-fg-2">
        The source behind it failed ({error.error}). Everything else on the page is current; it is retried with the next refresh.
      </p>
    </div>
  );
}

/** "Last test 5 h ago"-style hint next to data that is older than expected. */
export function StaleHint({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1 text-meta text-degraded', className)}>
      <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full shadow-[inset_0_0_0_1.5px_currentColor]" />
      {children}
    </span>
  );
}

/** Definition list in the E3 `.kv` style (label left, value right). */
export function KeyValues({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn('mt-5 grid grid-cols-[1fr_auto] gap-x-3 gap-y-2 border-t border-border pt-3.5 text-ui', className)}>
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-fg-2">{k}</dt>
          <dd className="num text-right font-medium text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
