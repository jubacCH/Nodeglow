'use client';

import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  /** Primary call-to-action shown below the description */
  action?: ReactNode;
  /** Optional secondary action (link or button) shown to the right of the primary */
  secondaryAction?: ReactNode;
  /** Apply on a card or directly on the page; default sizing fits a card */
  className?: string;
}

/**
 * Reusable empty state — use whenever a list or panel has nothing to show.
 *
 * Design intent: never leave the user staring at a void. Every empty state
 * gives them a concrete next step or, at minimum, explains why the panel
 * is empty so they can debug it themselves.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center px-6 py-12',
        className,
      )}
    >
      <div className="mb-4 p-3 rounded-full bg-slate-500/10">
        <Icon size={36} className="text-slate-500" />
      </div>
      <h3 className="text-base font-semibold text-slate-200 mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-slate-500 max-w-md mb-5">{description}</p>
      )}
      {(action || secondaryAction) && (
        <div className="flex items-center gap-3">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}
