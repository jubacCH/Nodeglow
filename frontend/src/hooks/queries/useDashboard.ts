import { useEffect, useRef } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { get, getCsrfToken } from '@/lib/api';
import { DASHBOARD_REFETCH_MS, DASHBOARD_V2_KEY, SUMMARY_V2_KEY } from '@/lib/dashboard';
import type {
  ChangeType, ChangesResponse, DashboardSummary, DashboardV2, HostPreview, IncidentPreview,
} from '@/types/dashboard';

/**
 * The E3 dashboard: one call for every card (GET /api/v2/dashboard).
 *
 * "Since your last visit" is pinned for the lifetime of the page: the first
 * response tells us the window start, and later refetches pass it as
 * `since`. Otherwise marking the dashboard as seen (useMarkDashboardSeen)
 * would empty the card while the user is still reading it.
 *
 * The embedded `summary` is written into the badge query, so the rail and
 * the dashboard always show the same numbers.
 */
export function useDashboardV2() {
  const qc = useQueryClient();
  const pinned = useRef<{ since: string; fallback: boolean } | null>(null);
  return useQuery({
    queryKey: DASHBOARD_V2_KEY,
    queryFn: async () => {
      const p = pinned.current;
      const data = await get<DashboardV2>(`/api/v2/dashboard${p ? `?since=${encodeURIComponent(p.since)}` : ''}`);
      const slv = data.since_last_visit;
      if (!p && slv?.since) pinned.current = { since: slv.since, fallback: slv.fallback };
      else if (p && slv) data.since_last_visit = { ...slv, fallback: p.fallback };
      if (data.summary) qc.setQueryData(SUMMARY_V2_KEY, data.summary);
      return data;
    },
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

/** Counts for the rail/tab-bar badges (GET /api/v2/summary). */
export function useSummary() {
  return useQuery({
    queryKey: SUMMARY_V2_KEY,
    queryFn: () => get<DashboardSummary>('/api/v2/summary'),
    refetchInterval: DASHBOARD_REFETCH_MS,
    staleTime: 10_000,
  });
}

export function useNavCounts() {
  return useQuery({
    queryKey: ['nav-counts'],
    queryFn: () => get<Record<string, number>>('/api/v2/nav-counts'),
    refetchInterval: 60_000,
  });
}

export interface ChangesParams {
  since: string;
  until?: string;
  types?: ChangeType[];
  limit?: number;
  offset?: number;
}

/** Change feed page (GET /api/v2/changes). */
export function useChanges({ since, until, types, limit = 50, offset = 0 }: ChangesParams, enabled = true) {
  const qs = new URLSearchParams({ since, limit: String(limit), offset: String(offset) });
  if (until) qs.set('until', until);
  if (types?.length) qs.set('types', types.join(','));
  return useQuery({
    queryKey: ['changes-v2', since, until ?? null, types?.join(',') ?? '', limit, offset],
    queryFn: () => get<ChangesResponse>(`/api/v2/changes?${qs.toString()}`),
    enabled,
    placeholderData: keepPreviousData,
  });
}

/** Incident with its affected hosts and their current state (side panel). */
export function useIncidentPreview(id: number | null) {
  return useQuery({
    queryKey: ['dashboard-incident-preview', id],
    queryFn: () => get<IncidentPreview>(`/api/v1/incidents/${id}`),
    enabled: id !== null,
    staleTime: 15_000,
  });
}

/** Host state, reason and freshness (side panel). */
export function useHostPreview(id: number | null) {
  return useQuery({
    queryKey: ['dashboard-host-preview', id],
    queryFn: () => get<HostPreview>(`/api/v1/hosts/${id}`),
    enabled: id !== null,
    staleTime: 15_000,
  });
}

/** Time on the dashboard after which it counts as seen. */
export const SEEN_AFTER_MS = 10_000;
/** Leaving earlier than this (a bounce, a quick reload) does not count. */
export const SEEN_MIN_DWELL_MS = 3_000;

function postSeen(keepalive: boolean) {
  // Plain fetch so `keepalive` survives page unload; CSRF like api.ts.
  return fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ''}/api/v2/me/seen`, {
    method: 'POST',
    credentials: 'include',
    keepalive,
    headers: { Accept: 'application/json', 'x-csrf-token': getCsrfToken() },
  }).catch(() => undefined);
}

/**
 * Marks the dashboard as seen (POST /api/v2/me/seen) after the user has
 * been on it for SEEN_AFTER_MS, and when they leave it (hidden tab,
 * pagehide, navigating away) — never on load, otherwise "since your last
 * visit" would be empty after every reload.
 */
export function useMarkDashboardSeen(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const mountedAt = Date.now();
    let lastSent = 0;
    const send = (keepalive: boolean, force = false) => {
      const now = Date.now();
      if (!force && now - mountedAt < SEEN_MIN_DWELL_MS) return;
      if (now - lastSent < 2_000) return;
      lastSent = now;
      void postSeen(keepalive);
    };
    const timer = window.setTimeout(() => send(false, true), SEEN_AFTER_MS);
    const onVisibility = () => { if (document.visibilityState === 'hidden') send(true); };
    const onPageHide = () => send(true);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      send(true);
    };
  }, [enabled]);
}

export interface HostSearchResult {
  id: number;
  name: string;
  hostname: string;
  enabled: boolean;
  online: boolean | null;
}

/**
 * Server-side host search (/hosts/api/search, name/hostname, max 10).
 * Only runs for queries of 2+ characters and while `enabled`.
 */
export function useHostSearch(q: string, enabled = true) {
  const query = q.trim();
  return useQuery({
    queryKey: ['host-search', query.toLowerCase()],
    queryFn: () => get<HostSearchResult[]>(`/hosts/api/search?q=${encodeURIComponent(query)}`),
    enabled: enabled && query.length >= 2,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}
