'use client';

import { cn } from '@/lib/utils';
import { HEALTH_LABEL, toHealthState, type HealthState, type LegacyStatus } from '@/lib/status';

export interface StatusDotProps {
  /** Design-system state, or one of the legacy names (online, offline …). */
  status: HealthState | LegacyStatus;
  size?: 'sm' | 'md' | 'lg';
  /**
   * Glow for down (strong) and warning (soft). On by default; healthy,
   * maintenance and unknown never glow regardless of this flag.
   */
  glow?: boolean;
  /** Slow breathing halo for a *critical, unacknowledged* state. Dark mode
   *  only; disabled by prefers-reduced-motion. */
  breathe?: boolean;
  /** @deprecated Use `breathe`. */
  pulse?: boolean;
  /** Screen-reader text. Defaults to the state label; pass "" when a visible
   *  label sits next to the dot. */
  label?: string;
  className?: string;
}

const size = { sm: 'h-[6px] w-[6px]', md: 'h-[8px] w-[8px]', lg: 'h-[10px] w-[10px]' } as const;

const fill: Record<HealthState, string> = {
  ok: 'bg-ok',
  degraded: 'bg-degraded',
  warning: 'bg-warning',
  down: 'bg-down',
  maint: 'bg-maint',
  // Hollow ring: "no light = no data".
  unknown: 'bg-transparent shadow-[inset_0_0_0_1.5px_var(--ng-st-unknown)]',
};

/**
 * Small status indicator. Colour + shape: unknown is a hollow ring,
 * maintenance is a muted grey-blue (never amber), down and warning glow.
 */
export function StatusDot({ status, size: s = 'md', glow = true, breathe, pulse, label, className }: StatusDotProps) {
  const state = toHealthState(status);
  const disabled = status === 'disabled';
  const sr = label ?? (disabled ? 'Disabled' : HEALTH_LABEL[state]);
  const wantsBreathe = (breathe ?? pulse) && state === 'down';
  return (
    <span className="relative inline-flex shrink-0" role={sr ? 'img' : undefined} aria-label={sr || undefined} aria-hidden={sr ? undefined : true}>
      <span
        className={cn(
          'block rounded-full',
          size[s],
          fill[state],
          disabled && 'opacity-60',
          glow && state === 'down' && 'shadow-glow-dot-crit',
          glow && state === 'warning' && 'shadow-glow-dot-warn',
          className,
        )}
      />
      {wantsBreathe && glow && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 animate-breathe rounded-full shadow-glow-dot-crit" />
      )}
    </span>
  );
}
