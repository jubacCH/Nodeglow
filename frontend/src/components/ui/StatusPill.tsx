'use client';

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { HEALTH_LABEL, STATE_SOFT, STATE_TEXT, toHealthState, type HealthState, type LegacyStatus } from '@/lib/status';

export interface StatusPillProps {
  status: HealthState | LegacyStatus;
  /** Visible text; defaults to the state label ("Down", "No data" …). */
  children?: ReactNode;
  /** Hide the leading dot (e.g. in very dense tables). */
  noDot?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * Semantic status pill: tinted background, AA text colour, dot. Use it for
 * state summaries ("1 critical", "9 no data") and status columns. Pills do
 * not glow; glow belongs to the object that needs attention.
 */
export function StatusPill({ status, children, noDot, size = 'md', className }: StatusPillProps) {
  const state = toHealthState(status);
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-pill font-medium',
        size === 'md' ? 'h-[22px] px-2 text-meta' : 'h-[18px] px-1.5 text-micro',
        STATE_SOFT[state],
        STATE_TEXT[state],
        className,
      )}
    >
      {!noDot && (
        <span
          aria-hidden="true"
          className={cn(
            'h-[6px] w-[6px] shrink-0 rounded-full',
            state === 'unknown' ? 'shadow-[inset_0_0_0_1.5px_currentColor]' : 'bg-current',
          )}
        />
      )}
      {children ?? HEALTH_LABEL[state]}
    </span>
  );
}
