/**
 * Folds WebSocket live events into the TanStack Query cache.
 *
 * The backend pushes a `ping_update` for every host check and an
 * `agent_metric` for every agent report. Instead of keeping a parallel copy
 * of that state in a store nobody reads, the events patch the cached query
 * data the pages already render, so status dots and latency stay live while
 * the polling intervals can be relaxed (see `liveRefetchInterval`).
 *
 * Every patch is a no-op (returns the same reference) when nothing in the
 * cached entry changed, so React Query does not notify observers needlessly.
 */
import type { QueryClient } from '@tanstack/react-query';
import type { Agent, HostDetail, HostStatus, WsAgentMetric, WsPingUpdate } from '@/types';
import type { DashboardData } from '@/hooks/queries/useDashboard';
import type { HostListItem } from '@/hooks/queries/useHosts';

/** Backend timestamps come from datetime.utcnow().isoformat() — no zone. */
export function toUtcIso(ts: string): string {
  return /[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : `${ts}Z`;
}

export function patchDashboard(
  data: DashboardData | undefined,
  updates: ReadonlyMap<number, WsPingUpdate>,
): DashboardData | undefined {
  if (!data?.host_stats || updates.size === 0) return data;
  let changed = false;
  const host_stats = data.host_stats.map((hs) => {
    const u = updates.get(hs.host.id);
    if (!u || (hs.online === u.online && hs.latency === u.latency_ms)) return hs;
    changed = true;
    return { ...hs, online: u.online, latency: u.latency_ms };
  });
  if (!changed) return data;
  // Same rule as the backend: maintenance hosts do not count.
  const active = host_stats.filter((s) => !s.host.maintenance);
  return {
    ...data,
    host_stats,
    online_count: active.filter((s) => s.online === true).length,
    offline_count: active.filter((s) => s.online === false).length,
  };
}

export function patchHostStatusList(
  data: HostStatus[] | undefined,
  updates: ReadonlyMap<number, WsPingUpdate>,
): HostStatus[] | undefined {
  if (!data || updates.size === 0) return data;
  let changed = false;
  const next = data.map((h) => {
    const u = updates.get(h.id);
    if (!u || (h.online === u.online && h.latency_ms === u.latency_ms)) return h;
    changed = true;
    return {
      ...h,
      online: u.online,
      latency_ms: u.latency_ms,
      last_seen: u.online ? toUtcIso(u.ts) : h.last_seen,
    };
  });
  return changed ? next : data;
}

export function patchHostListV1(
  data: HostListItem[] | undefined,
  updates: ReadonlyMap<number, WsPingUpdate>,
): HostListItem[] | undefined {
  if (!data || updates.size === 0) return data;
  let changed = false;
  const next = data.map((h) => {
    const u = updates.get(h.id);
    if (!u) return h;
    // Disabled and maintenance win over the check result, as in the API.
    const status: HostListItem['status'] =
      !h.enabled ? 'disabled'
        : h.maintenance ? 'maintenance'
          : u.online ? 'online' : 'offline';
    if (h.status === status && h.latency_ms === u.latency_ms) return h;
    changed = true;
    return { ...h, status, latency_ms: u.latency_ms, last_check: toUtcIso(u.ts) };
  });
  return changed ? next : data;
}

export function patchHostDetail(
  data: HostDetail | undefined,
  u: WsPingUpdate,
): HostDetail | undefined {
  if (!data) return data;
  if (data.latest && data.latest.online === u.online && data.latest.latency_ms === u.latency_ms) {
    return data;
  }
  return {
    ...data,
    latest: { online: u.online, latency_ms: u.latency_ms, timestamp: toUtcIso(u.ts) },
  };
}

export function patchAgentList(
  data: Agent[] | undefined,
  updates: ReadonlyMap<number, WsAgentMetric>,
): Agent[] | undefined {
  if (!data || updates.size === 0) return data;
  let changed = false;
  const next = data.map((a) => {
    const m = updates.get(a.id);
    if (!m) return a;
    changed = true;
    return {
      ...a,
      online: true,
      last_seen: toUtcIso(m.ts),
      cpu_pct: m.cpu_pct,
      mem_pct: m.mem_pct,
      disk_pct: m.disk_pct,
    };
  });
  return changed ? next : data;
}

/**
 * Write `patch(current)` back only when it produced a new object. The
 * entry keeps its original dataUpdatedAt: a live patch is not a refetch, so
 * staleness and "updated N s ago" labels stay tied to the last real fetch.
 */
function patchQuery<T>(qc: QueryClient, key: readonly unknown[], patch: (d: T | undefined) => T | undefined) {
  const state = qc.getQueryState<T>(key);
  if (!state || state.data === undefined) return;
  const next = patch(state.data);
  if (next === undefined || next === state.data) return;
  qc.setQueryData<T>(key, next, { updatedAt: state.dataUpdatedAt });
}

const REACHABLE_STATES = new Set(['up', 'degraded', 'warning']);

/**
 * True when a ping result contradicts a cached unified `state` (reachable vs.
 * down/unknown). The unified state is derived server-side (probes,
 * maintenance, check errors), so the client refetches instead of guessing it.
 */
export function hasStateFlip(
  data: HostListItem[] | undefined,
  updates: ReadonlyMap<number, WsPingUpdate>,
): boolean {
  if (!data || updates.size === 0) return false;
  return data.some((h) => {
    const u = updates.get(h.id);
    if (!u || !h.state || h.state === 'maintenance' || h.state === 'disabled') return false;
    return REACHABLE_STATES.has(h.state) !== u.online;
  });
}

/** Apply one batch of buffered events to every cache entry they affect. */
export function applyLiveUpdates(
  qc: QueryClient,
  pings: ReadonlyMap<number, WsPingUpdate>,
  agents: ReadonlyMap<number, WsAgentMetric>,
): void {
  if (pings.size > 0) {
    const flipped = qc
      .getQueriesData<HostListItem[]>({ queryKey: ['hosts-v1'] })
      .some(([, d]) => hasStateFlip(d, pings));
    if (flipped) void qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    patchQuery<DashboardData>(qc, ['dashboard'], (d) => patchDashboard(d, pings));
    patchQuery<HostStatus[]>(qc, ['hosts'], (d) => patchHostStatusList(d, pings));
    patchQuery<HostListItem[]>(qc, ['hosts-v1'], (d) => patchHostListV1(d, pings));
    pings.forEach((u, id) => {
      patchQuery<HostDetail>(qc, ['host', id], (d) => patchHostDetail(d, u));
    });
  }
  if (agents.size > 0) {
    patchQuery<Agent[]>(qc, ['agents'], (d) => patchAgentList(d, agents));
  }
}

/**
 * refetchInterval for queries whose live parts are kept current by the
 * WebSocket: poll at `fast` while the socket is down, `slow` while it is up.
 * Evaluated by React Query whenever the query updates.
 */
export function liveRefetchInterval(isConnected: () => boolean, fast: number, slow: number) {
  return () => (isConnected() ? slow : fast);
}
