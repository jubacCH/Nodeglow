'use client';

import { cn, severityColor } from '@/lib/utils';
import type { HTMLAttributes } from 'react';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'severity' | 'integration';
  severity?: 'critical' | 'warning' | 'info';
  color?: string;
}

export function Badge({ className, variant = 'default', severity, color, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border',
        variant === 'severity' && severity && severityColor(severity),
        variant === 'integration' && color && `border-current`,
        variant === 'default' && 'bg-white/[0.06] text-slate-300 border-white/[0.08]',
        className,
      )}
      style={variant === 'integration' && color ? { color, borderColor: `${color}40` } : undefined}
      {...props}
    >
      {children}
    </span>
  );
}
