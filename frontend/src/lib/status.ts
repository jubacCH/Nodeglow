/**
 * Status vocabulary of the design system (IA §6.5). Every state has a colour,
 * a shape and a text label. Unknown/stale never looks healthy: it is grey,
 * hollow or hatched, and it never glows.
 */
export type HealthState = 'ok' | 'degraded' | 'warning' | 'down' | 'maint' | 'unknown';

/** Older status names used across the app, mapped onto HealthState. */
export type LegacyStatus = 'online' | 'offline' | 'error' | 'maintenance' | 'disabled';

export const HEALTH_STATES: HealthState[] = ['ok', 'degraded', 'warning', 'down', 'maint', 'unknown'];

export const HEALTH_LABEL: Record<HealthState, string> = {
  ok: 'OK',
  degraded: 'Degraded',
  warning: 'Warning',
  down: 'Down',
  maint: 'Maintenance',
  unknown: 'No data',
};

export function toHealthState(status: HealthState | LegacyStatus | string | null | undefined): HealthState {
  switch (status) {
    case 'ok': case 'online': case 'up': return 'ok';
    case 'degraded': return 'degraded';
    case 'warning': case 'error': return 'warning';
    case 'down': case 'offline': case 'critical': return 'down';
    case 'maint': case 'maintenance': return 'maint';
    default: return 'unknown';
  }
}

/** Only these states may glow (E3 glow rule). */
export function glows(state: HealthState): 'crit' | 'warn' | null {
  if (state === 'down') return 'crit';
  if (state === 'warning') return 'warn';
  return null;
}

/** Background class for dots, bars and legend swatches. Unknown is hatched. */
export const STATE_FILL: Record<HealthState, string> = {
  ok: 'bg-ok',
  degraded: 'bg-degraded',
  warning: 'bg-warning',
  down: 'bg-down',
  maint: 'bg-maint',
  unknown: 'ng-hatch',
};

/** AA-checked text colour per state. */
export const STATE_TEXT: Record<HealthState, string> = {
  ok: 'text-ok',
  degraded: 'text-degraded',
  warning: 'text-warning',
  down: 'text-down',
  maint: 'text-maint',
  unknown: 'text-unknown',
};

/** Soft tinted background (pills, chips). */
export const STATE_SOFT: Record<HealthState, string> = {
  ok: 'bg-ok-soft',
  degraded: 'bg-degraded-soft',
  warning: 'bg-warning-soft',
  down: 'bg-down-soft',
  maint: 'bg-maint-soft',
  unknown: 'bg-unknown-soft',
};

/** One slice of a state distribution (HealthRing, status bars). */
export interface HealthSegment {
  state: HealthState;
  count: number;
  /** Overrides the default label ("Down", "No data" …). */
  label?: string;
  /** Short reason, shown in the legend and the segment tooltip. */
  detail?: string;
}

/** Accessible summary: "48 hosts: 29 ok, 5 degraded, 2 down, 9 no data". */
export function describeSegments(segments: HealthSegment[], noun = 'hosts'): string {
  const total = segments.reduce((a, s) => a + s.count, 0);
  const parts = segments
    .filter((s) => s.count > 0)
    .map((s) => `${s.count} ${(s.label ?? HEALTH_LABEL[s.state]).toLowerCase()}`);
  return `${total} ${noun}: ${parts.join(', ') || 'none'}`;
}

/** CSS colour for SVG strokes/fills (unknown uses the hatch pattern). */
export const STATE_VAR: Record<HealthState, string> = {
  ok: 'var(--ng-st-ok)',
  degraded: 'var(--ng-st-degraded)',
  warning: 'var(--ng-st-warning)',
  down: 'var(--ng-st-down)',
  maint: 'var(--ng-st-maint)',
  unknown: 'var(--ng-st-unknown)',
};
