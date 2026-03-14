'use client';

import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface GlassCardProps extends HTMLAttributes<HTMLDivElement> {
  elevated?: boolean;
}

export const GlassCard = forwardRef<HTMLDivElement, GlassCardProps>(
  ({ className, elevated, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-lg border transition-all backdrop-blur-xl',
        elevated ? 'glass-elevated' : 'glass-card',
        className,
      )}
      style={{
        background: elevated ? 'var(--ng-glass-bg-elevated)' : 'var(--ng-glass-bg)',
        borderColor: elevated ? 'var(--ng-glass-border-elevated)' : 'var(--ng-glass-border)',
      }}
      {...props}
    >
      {children}
    </div>
  ),
);
GlassCard.displayName = 'GlassCard';
