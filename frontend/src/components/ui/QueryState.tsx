'use client';

import type { ReactNode } from 'react';
import { AlertTriangle, Clock, Lock, RotateCcw } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';
import { Skeleton } from './Skeleton';

/** The subset of a TanStack Query result that QueryState looks at. */
export interface QueryLike<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
  isFetching?: boolean;
  /** ms timestamp of the last successful fetch (TanStack `dataUpdatedAt`). */
  dataUpdatedAt?: number;
}

/** Human-readable reason for a failed query. */
export function describeQueryError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'You do not have permission to view this.';
    if (error.status === 404) return 'Not found.';
    if (error.status >= 500) return `The server returned an error (${error.status}).`;
    return error.message;
  }
  if (error instanceof TypeError) return 'Could not reach the server.';
  if (error instanceof Error) return error.message;
  return 'Something went wrong.';
}

function isForbidden(error: unknown) {
  return error instanceof ApiError && error.status === 403;
}

/** "14:32" for today, otherwise a short date + time. */
export function formatAsOf(ts: number | undefined | null): string | null {
  if (!ts) return null;
  const d = new Date(ts);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

interface QueryErrorStateProps {
  error: unknown;
  onRetry?: () => unknown;
  title?: string;
  /** Smaller layout for table cells and cards. */
  compact?: boolean;
  className?: string;
}

/** Error panel with a retry button, so a failed request never looks "empty". */
export function QueryErrorState({ error, onRetry, title, compact, className }: QueryErrorStateProps) {
  const forbidden = isForbidden(error);
  const Icon = forbidden ? Lock : AlertTriangle;
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-1.5 px-4 py-6' : 'gap-2 px-6 py-12',
        className,
      )}
    >
      <Icon size={compact ? 20 : 28} className={forbidden ? 'text-fg-3' : 'text-warning'} aria-hidden="true" />
      <p className={cn('font-medium text-fg', compact ? 'text-ui' : 'text-body')}>
        {title ?? (forbidden ? 'No access' : 'Could not load data')}
      </p>
      <p className="max-w-md text-meta text-fg-2">{describeQueryError(error)}</p>
      {onRetry && !forbidden && (
        <button
          type="button"
          onClick={() => onRetry()}
          className="mt-1 inline-flex h-[28px] items-center gap-1.5 rounded-ng-sm border border-border-2 bg-surface-2 px-2.5 text-meta font-medium text-fg hover:bg-surface-3"
        >
          <RotateCcw size={12} aria-hidden="true" /> Retry
        </button>
      )}
    </div>
  );
}

interface StaleDataBannerProps {
  error?: unknown;
  onRetry?: () => unknown;
  /** ms timestamp of the data still on screen. */
  updatedAt?: number;
  className?: string;
}

/**
 * Thin banner for "showing cached data, the last refresh failed". Stale data
 * is labelled with its age so it never passes for live data.
 */
export function StaleDataBanner({ error, onRetry, updatedAt, className }: StaleDataBannerProps) {
  const asOf = formatAsOf(updatedAt);
  return (
    <div
      role="status"
      className={cn(
        'mb-3 flex items-center gap-2 rounded-ctl border border-degraded/40 bg-degraded-soft px-3 py-2 text-meta text-degraded',
        className,
      )}
    >
      <Clock size={14} aria-hidden="true" className="shrink-0" />
      <span className="flex-1">
        {asOf ? `Showing data from ${asOf}. ` : 'Showing the last data received. '}
        Refresh failed{error ? `: ${describeQueryError(error)}` : '.'}
      </span>
      {onRetry && (
        <button type="button" onClick={() => onRetry()} className="font-medium underline underline-offset-2">
          Retry
        </button>
      )}
    </div>
  );
}

interface QueryStateProps<T> {
  query: QueryLike<T>;
  /** Shown while the first load is in flight. Defaults to a few skeleton rows. */
  loading?: ReactNode;
  /** Shown when the data loaded fine but `isEmpty(data)` is true. */
  empty?: ReactNode;
  /** Defaults to "is an empty array". */
  isEmpty?: (data: T) => boolean;
  errorTitle?: string;
  /** Compact error layout (inside cards). */
  compact?: boolean;
  children: (data: T) => ReactNode;
}

/**
 * One place for the loading / error / empty / data branches of a query.
 * A refetch that fails while older data is cached keeps the data on screen
 * and adds a stale banner (with the data's age) instead of replacing it.
 */
export function QueryState<T>({ query, loading, empty, isEmpty, errorTitle, compact, children }: QueryStateProps<T>) {
  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = query;

  if (isLoading) {
    return (
      <>
        {loading ?? (
          <div className="space-y-2" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-3/4" />
          </div>
        )}
      </>
    );
  }

  if (data === undefined) {
    if (isError) return <QueryErrorState error={error} onRetry={refetch} title={errorTitle} compact={compact} />;
    return <>{empty ?? null}</>;
  }

  const emptyCheck = isEmpty ?? ((d: T) => Array.isArray(d) && d.length === 0);
  return (
    <>
      {isError && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}
      {emptyCheck(data) && empty !== undefined ? empty : children(data)}
    </>
  );
}
