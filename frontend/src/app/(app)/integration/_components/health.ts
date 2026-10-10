import type { HealthState } from '@/lib/status';
import type { IntegrationConfig } from '@/types';

/** Integrations that poll far less often than the default 60 s. */
const SLOW_TYPES: Record<string, number> = { speedtest: 3 * 3600_000 };
const DEFAULT_STALE_MS = 15 * 60_000;

/** Age after which the latest snapshot no longer counts as a fresh observation. */
export function staleAfterMs(type: string): number {
  return SLOW_TYPES[type] ?? DEFAULT_STALE_MS;
}

export interface IntegrationHealth {
  status: HealthState | 'disabled';
  label: string;
  /** Short reason for the state, shown next to it. */
  reason: string;
  /** True when the last collection succeeded (fresh or not). */
  lastOk: boolean;
}

/**
 * Honest integration health: an error or a missing/old snapshot is never
 * shown as healthy. `last_check` is the time of the latest collection
 * attempt; it only counts as "last successful collect" when it succeeded.
 */
export function integrationHealth(
  i: { type: string; enabled: boolean; status?: IntegrationConfig['status']; ok?: boolean; is_standby?: boolean },
  lastCheck: string | null | undefined,
  now = Date.now(),
): IntegrationHealth {
  const status = i.status ?? (lastCheck ? (i.is_standby ? 'standby' : i.ok ? 'ok' : 'error') : 'no_data');
  if (!i.enabled) return { status: 'disabled', label: 'Disabled', reason: 'Collection is turned off', lastOk: status === 'ok' || status === 'standby' };
  if (status === 'no_data' || !lastCheck) return { status: 'unknown', label: 'No data', reason: 'No collection has completed yet', lastOk: false };
  if (status === 'error') return { status: 'down', label: 'Error', reason: 'Last collection failed', lastOk: false };
  const age = now - new Date(lastCheck).getTime();
  if (age > staleAfterMs(i.type)) {
    return { status: 'unknown', label: 'Stale', reason: `No fresh data for ${formatAge(age)}`, lastOk: true };
  }
  if (status === 'standby') return { status: 'ok', label: 'Standby', reason: 'Reachable, passive HA member', lastOk: true };
  return { status: 'ok', label: 'OK', reason: 'Last collection succeeded', lastOk: true };
}

export function formatAge(ms: number): string {
  const m = Math.round(ms / 60_000);
  if (m < 60) return `${Math.max(1, m)} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.round(h / 24)} d`;
}
