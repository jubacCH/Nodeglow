/**
 * Unified host state as the API reports it (backend services/host_state.py)
 * and how the hosts pages present it. The design-system vocabulary
 * (lib/status.ts) already maps every value: up → ok, maintenance → maint,
 * disabled → the dimmed dot. This file only adds the host-specific labels,
 * the severity order and the freshness wording.
 */
import { STATE_TEXT, toHealthState, type HealthState } from '../../lib/status';
import { timeAgo } from '../../lib/utils';

export type HostState = 'up' | 'degraded' | 'warning' | 'down' | 'unknown' | 'maintenance' | 'disabled';

/** Order of the filter chips. */
export const HOST_STATES: HostState[] = ['up', 'degraded', 'warning', 'down', 'unknown', 'maintenance', 'disabled'];

export const HOST_STATE_LABEL: Record<HostState, string> = {
  up: 'Up',
  degraded: 'Degraded',
  warning: 'Warning',
  down: 'Down',
  unknown: 'No data',
  maintenance: 'Maintenance',
  disabled: 'Disabled',
};

/** Worst first, same order as the backend's STATES (used for sorting). */
const SEVERITY: Record<HostState, number> = {
  down: 0, warning: 1, degraded: 2, unknown: 3, maintenance: 4, up: 5, disabled: 6,
};

export function hostStateRank(state: HostState): number {
  return SEVERITY[state];
}

export interface HostStateFields {
  state?: string | null;
  state_reason?: string | null;
  /** Time of the newest real check (ISO, UTC); set even when the state is unknown. */
  observed_at?: string | null;
}

/** Anything the API did not send (older backend, new value) is "unknown", never "up". */
export function normalizeHostState(raw: string | null | undefined): HostState {
  return (HOST_STATES as string[]).includes(raw ?? '') ? (raw as HostState) : 'unknown';
}

/** Status prop for <StatusDot>/<StatusPill>: the design-system state, or `disabled`. */
export function hostStatusProp(state: HostState): HealthState | 'disabled' {
  return state === 'disabled' ? 'disabled' : toHealthState(state);
}

/** AA text colour per host state (disabled is plain secondary text). */
export const STATE_TEXT_CLASS: Record<HostState, string> = {
  up: STATE_TEXT.ok,
  degraded: STATE_TEXT.degraded,
  warning: STATE_TEXT.warning,
  down: STATE_TEXT.down,
  unknown: STATE_TEXT.unknown,
  maintenance: STATE_TEXT.maint,
  disabled: 'text-fg-2',
};

/** Legacy `?status=` links (dashboard cards) → unified state. */
export function stateFromLegacyParam(param: string | null): HostState | null {
  switch (param) {
    case null: case '': case 'all': return null;
    case 'online': return 'up';
    case 'offline': return 'down';
    case 'error': return 'warning';
    default: return (HOST_STATES as string[]).includes(param) ? (param as HostState) : null;
  }
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/**
 * Freshness in words. Unknown never reads like a healthy reading:
 * "No data since 14:02 (12m ago)" / "Never observed".
 */
export function observedText(state: HostState, observedAt: string | null | undefined): string {
  if (state === 'disabled') return observedAt ? `Monitoring off · last check ${timeAgo(observedAt)}` : 'Monitoring off';
  if (!observedAt) return state === 'maintenance' ? 'No check yet' : 'Never observed';
  if (state === 'unknown') return `No data since ${formatDateTime(observedAt)} (${timeAgo(observedAt)})`;
  return `Observed ${timeAgo(observedAt)}`;
}

/** Compact freshness for table cells: "3m ago", "—". */
export function observedShort(observedAt: string | null | undefined): string {
  return observedAt ? timeAgo(observedAt) : '—';
}

/** Default reason when the API sends none. */
export function reasonText(state: HostState, reason: string | null | undefined): string {
  if (reason) return reason;
  switch (state) {
    case 'up': return 'Responding normally';
    case 'unknown': return 'Not observed: no recent check result';
    case 'disabled': return 'Monitoring is switched off';
    case 'maintenance': return 'In maintenance';
    default: return HOST_STATE_LABEL[state];
  }
}
