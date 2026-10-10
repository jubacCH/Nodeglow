'use client';

import type { HTMLAttributes } from 'react';
import { cn, severityColor } from '@/lib/utils';

export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'degraded' | 'warning' | 'down' | 'maint' | 'unknown';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  /** `severity` maps critical/warning/info onto status tones; `integration`
   *  draws a neutral outline badge (integration colours are not used: they
   *  would compete with status colours). */
  variant?: 'default' | 'severity' | 'integration';
  severity?: 'critical' | 'warning' | 'info';
  tone?: BadgeTone;
  /** @deprecated Ignored; integration badges are neutral in E3. */
  color?: string;
}

const tones: Record<BadgeTone, string> = {
  neutral: 'bg-surface-2 text-fg-2 border-border-2',
  accent: 'bg-accent-soft text-accent border-accent/30',
  ok: 'bg-ok-soft text-ok border-ok/30',
  degraded: 'bg-degraded-soft text-degraded border-degraded/30',
  warning: 'bg-warning-soft text-warning border-warning/30',
  down: 'bg-down-soft text-down border-down/30',
  maint: 'bg-maint-soft text-maint border-maint/30',
  unknown: 'bg-unknown-soft text-unknown border-border-2',
};

/**
 * Small rectangular label for counts, types and categories. For states use
 * <StatusPill>, for "planned"/meta labels use <Tag>.
 */
export function Badge({ className, variant = 'default', severity, tone = 'neutral', color: _color, children, ...props }: BadgeProps) {
  void _color;
  return (
    <span
      className={cn(
        'inline-flex h-[20px] items-center gap-1 whitespace-nowrap rounded-chip border px-1.5 text-micro font-medium',
        variant === 'severity' && severity ? severityColor(severity) : tones[variant === 'integration' ? 'neutral' : tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
