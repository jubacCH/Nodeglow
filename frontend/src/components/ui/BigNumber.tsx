import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { STATE_TEXT, type HealthState } from '@/lib/status';

export interface BigNumberProps {
  /** The figure. `null`/`undefined` renders an em dash in grey (no data is
   *  never shown as 0). */
  value: ReactNode | null | undefined;
  unit?: ReactNode;
  /** Small label under the number. */
  label?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  /** Colour the figure by state (only when the number *is* the state). */
  state?: HealthState;
  /** Dim the value and mark it as outdated. */
  stale?: boolean;
  className?: string;
}

const sizes = { sm: 'text-num-sm', md: 'text-num', lg: 'text-num-lg' } as const;

/**
 * Key figure in Sora with tabular numerals (E3 `.big`). Units sit in the UI
 * font, smaller and muted.
 */
export function BigNumber({ value, unit, label, size = 'md', state, stale, className }: BigNumberProps) {
  const empty = value === null || value === undefined || value === '';
  return (
    <div className={cn('min-w-0', className)}>
      <div
        className={cn(
          'num font-display font-medium leading-[1.05] tracking-[-0.045em]',
          sizes[size],
          empty ? 'text-fg-3' : state ? STATE_TEXT[state] : 'text-fg',
          stale && 'opacity-60',
        )}
      >
        {empty ? '—' : value}
        {unit && !empty && (
          <small className="ml-1.5 font-sans text-body font-normal tracking-normal text-fg-2">{unit}</small>
        )}
      </div>
      {label && <div className="mt-1 text-meta text-fg-2">{label}{stale && <span className="sr-only"> (outdated)</span>}</div>}
    </div>
  );
}
