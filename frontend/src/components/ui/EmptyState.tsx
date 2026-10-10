'use client';

import type { LucideIcon } from 'lucide-react';
import { CheckCircle2, Inbox } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  /**
   * - `not-configured`: feature has no source yet (neutral, offer setup)
   * - `no-results`: filters match nothing (neutral, offer reset)
   * - `confirmed`: positively checked and fresh, e.g. 0 open incidents.
   *   Green, and requires `asOf` — "empty" is only good news with a timestamp.
   */
  variant?: 'not-configured' | 'no-results' | 'confirmed';
  /** Timestamp of the check behind a `confirmed` empty state. */
  asOf?: ReactNode;
  action?: ReactNode;
  secondaryAction?: ReactNode;
  /** Smaller layout inside cards. */
  compact?: boolean;
  className?: string;
}

/** Empty state. Never leave a void: say why it is empty and what to do next. */
export function EmptyState({
  icon,
  title,
  description,
  variant = 'not-configured',
  asOf,
  action,
  secondaryAction,
  compact,
  className,
}: EmptyStateProps) {
  const confirmed = variant === 'confirmed';
  const Icon = icon ?? (confirmed ? CheckCircle2 : Inbox);
  return (
    <div
      role={confirmed ? 'status' : undefined}
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-1.5 px-4 py-6' : 'gap-2 px-6 py-12',
        className,
      )}
    >
      <span
        className={cn(
          'mb-1 grid place-items-center rounded-card',
          compact ? 'h-9 w-9' : 'h-11 w-11',
          confirmed ? 'bg-ok-soft text-ok' : 'bg-surface-2 text-fg-3',
        )}
        aria-hidden="true"
      >
        <Icon size={compact ? 18 : 22} />
      </span>
      <h3 className={cn('font-medium text-fg', compact ? 'text-ui' : 'text-body')}>{title}</h3>
      {description && <p className="max-w-md text-ui text-fg-2">{description}</p>}
      {confirmed && asOf && <p className="text-meta text-fg-3">As of {asOf}</p>}
      {(action || secondaryAction) && (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}
