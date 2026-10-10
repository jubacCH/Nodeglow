'use client';

import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface GlassCardProps extends HTMLAttributes<HTMLDivElement> {
  elevated?: boolean;
}

/**
 * @deprecated Use <Card> from '@/components/ui/Card'. Kept so unmigrated
 * pages render with the E3 card surface (no glassmorphism any more).
 */
export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(
  ({ className, elevated, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'min-w-0 rounded-card border transition-colors duration-150',
        elevated ? 'border-border-2 bg-surface-2' : 'border-border bg-surface',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  ),
);
GlassCard.displayName = 'GlassCard';
