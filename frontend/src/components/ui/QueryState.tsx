'use client';

import type { ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
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

interface QueryErrorStateProps {
  error: unknown;
  onRetry?: () => unknown;
  title?: string;
  /** Smaller layout for table cells and cards. */
  compact?: boolean;
  className?: string;
}

/** Error panel with a retry button — so a failed request never looks "empty". */
export function QueryErrorState({ error, onRetry, title = 'Could not load data', compact, className }: QueryErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-1.5 px-4 py-6' : 'gap-2 px-6 py-12',
        className,
      )}
    >
      <AlertTriangle size={compact ? 20 : 32} className="text-amber-400" aria-hidden="true" />
      <p className={cn('font-medium text-slate-200', compact ? 'text-sm' : 'text-base')}>{title}</p>
      <p className="text-xs text-slate-500 max-w-md">{describeQueryError(error)}</p>
      {onRetry && (
        <button
          type="button"
          onClick={() => onRetry()}
          className="mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs text-slate-300 bg-white/[0.04] border border-white/[0.08] hover:bg-white/[0.08] transition-colors"
        >
          <RotateCcw size={12} aria-hidden="true" /> Retry
        </button>
      )}
    </div>
  );
}

/** Thin banner for "showing cached data, the last refresh failed". */
export function StaleDataBanner({ error, onRetry }: { error: unknown; onRetry?: () => unknown }) {
  return (
    <div role="status" className="flex items-center gap-2 px-3 py-2 mb-3 rounded-md text-xs bg-amber-500/10 border border-amber-500/20 text-amber-300">
      <AlertTriangle size={14} aria-hidden="true" />
      <span className="flex-1">Refresh failed — showing the last data received. {describeQueryError(error)}</span>
      {onRetry && (
        <button type="button" onClick={() => onRetry()} className="underline hover:text-amber-200">
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
  children: (data: T) => ReactNode;
}

/**
 * One place for the loading / error / empty / data branches of a query.
 * A refetch that fails while older data is cached keeps the data on screen
 * and adds a banner instead of replacing it.
 */
export function QueryState<T>({ query, loading, empty, isEmpty, errorTitle, children }: QueryStateProps<T>) {
  const { data, isLoading, isError, error, refetch } = query;

  if (isLoading) {
    return (
      <>
        {loading ?? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-3/4" />
          </div>
        )}
      </>
    );
  }

  if (data === undefined) {
    if (isError) return <QueryErrorState error={error} onRetry={refetch} title={errorTitle} />;
    return <>{empty ?? null}</>;
  }

  const emptyCheck = isEmpty ?? ((d: T) => Array.isArray(d) && d.length === 0);
  return (
    <>
      {isError && <StaleDataBanner error={error} onRetry={refetch} />}
      {emptyCheck(data) && empty !== undefined ? empty : children(data)}
    </>
  );
}
