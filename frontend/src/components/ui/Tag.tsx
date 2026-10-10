'use client';

import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  /** Dashed outline for things that are planned / not available yet. */
  planned?: boolean;
  children: ReactNode;
}

/**
 * Outline tag for meta information ("Multi-tenant · Planned", "Prototype",
 * source labels). `planned` draws it dashed: the convention for features that
 * are announced but not usable yet.
 */
export function Tag({ planned, className, children, ...props }: TagProps) {
  return (
    <span
      className={cn(
        'inline-flex h-[20px] items-center whitespace-nowrap rounded-chip border border-border-2 px-[7px] text-micro font-medium text-fg-2',
        planned && 'border-dashed',
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: 'neutral' | 'accent';
  /** Leading dot in the current colour. */
  dot?: boolean;
  children: ReactNode;
}

/** Rounded pill for neutral or accent information (filters, selections).
 *  Status pills: <StatusPill>. */
export function Pill({ tone = 'neutral', dot, className, children, ...props }: PillProps) {
  return (
    <span
      className={cn(
        'inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-pill px-2 text-meta font-medium',
        tone === 'accent' ? 'bg-accent-soft text-accent' : 'bg-surface-2 text-fg-2',
        className,
      )}
      {...props}
    >
      {dot && <span aria-hidden="true" className="h-[6px] w-[6px] rounded-full bg-current" />}
      {children}
    </span>
  );
}
