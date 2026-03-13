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
        'rounded-lg border transition-all',
        elevated
          ? 'bg-white/[0.06] backdrop-blur-2xl border-white/[0.08] shadow-2xl'
          : 'bg-white/[0.04] backdrop-blur-xl border-white/[0.06]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  ),
);
GlassCard.displayName = 'GlassCard';
