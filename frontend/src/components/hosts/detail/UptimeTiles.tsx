'use client';

import { BigNumber } from '@/components/ui/BigNumber';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import type { HealthState } from '@/lib/status';
import { formatDateTime, type HostState } from '../hostState';
import type { HostDetailData } from './types';

const OBSERVED: ReadonlySet<HostState> = new Set(['up', 'degraded', 'warning', 'down']);

function uptimeState(v: number | null, observed: boolean): HealthState | undefined {
  // History of a host nobody watches right now stays neutral (never green).
  if (v == null || !observed) return undefined;
  if (v >= 99.9) return 'ok';
  if (v >= 95) return 'degraded';
  return 'down';
}

function pct(v: number | null): string | null {
  if (v == null) return null;
  return v >= 100 ? '100' : v.toFixed(v >= 99 ? 2 : 1);
}

/** Uptime 24h / 7d / 30d and the latest latency. "—" when there is no data, never 0. */
export function UptimeTiles({ host, state, loading }: { host?: HostDetailData; state: HostState; loading?: boolean }) {
  if (loading || !host) {
    return (
      <div className="grid grid-cols-4 gap-4 max-[999px]:grid-cols-2" aria-busy="true" aria-label="Loading uptime">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}><Skeleton className="mb-2 h-9 w-24" /><Skeleton className="h-4 w-16" /></Card>
        ))}
      </div>
    );
  }
  const observed = OBSERVED.has(state);
  const lat = host.latest?.latency_ms ?? null;
  const tiles = [
    { key: 'h24', label: 'Uptime 24h', value: host.uptime?.h24 ?? null },
    { key: 'd7', label: 'Uptime 7d', value: host.uptime?.d7 ?? null },
    { key: 'd30', label: 'Uptime 30d', value: host.uptime?.d30 ?? null },
  ];
  return (
    <div className="grid grid-cols-4 gap-4 max-[999px]:grid-cols-2">
      {tiles.map((t) => (
        <Card key={t.key}>
          <BigNumber size="sm" value={pct(t.value)} unit="%" label={t.label} state={uptimeState(t.value, observed)} />
        </Card>
      ))}
      <Card>
        <BigNumber
          size="sm"
          value={lat == null ? null : lat < 1 ? '<1' : Math.round(lat)}
          unit="ms"
          stale={!observed}
          state={state === 'degraded' && lat != null ? 'degraded' : undefined}
          label={
            observed
              ? `Latency · ${host.latency_threshold_ms ? `threshold ${host.latency_threshold_ms} ms` : 'latest check'}`
              : host.latest?.timestamp
                ? `Last latency, ${formatDateTime(host.latest.timestamp)} (not current)`
                : 'Latency · no data'
          }
        />
      </Card>
    </div>
  );
}
