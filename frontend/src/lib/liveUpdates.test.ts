import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  applyLiveUpdates,
  hasStateFlip,
  patchHostListV1,
  patchHostStatusList,
  toUtcIso,
} from './liveUpdates';
import type { HostStatus, WsPingUpdate } from '../types';
import type { HostListItem } from '../hooks/queries/useHosts';

function ping(host_id: number, online: boolean, latency_ms: number | null = 1): WsPingUpdate {
  return { type: 'ping_update', ts: '2026-10-10T12:00:00', host_id, name: `h${host_id}`, online, latency_ms };
}

describe('toUtcIso', () => {
  it('marks zone-less backend timestamps as UTC', () => {
    expect(toUtcIso('2026-10-10T12:00:00')).toBe('2026-10-10T12:00:00Z');
    expect(toUtcIso('2026-10-10T12:00:00+02:00')).toBe('2026-10-10T12:00:00+02:00');
    expect(toUtcIso('2026-10-10T12:00:00Z')).toBe('2026-10-10T12:00:00Z');
  });
});

describe('patchHostStatusList', () => {
  it('patches online/latency and last_seen on success', () => {
    const list = [{ id: 1, online: false, latency_ms: null, last_seen: null }] as unknown as HostStatus[];
    const next = patchHostStatusList(list, new Map([[1, ping(1, true, 4)]]))!;
    expect(next[0]).toMatchObject({ online: true, latency_ms: 4, last_seen: '2026-10-10T12:00:00Z' });
  });
});

describe('patchHostListV1', () => {
  it('keeps disabled/maintenance status over the check result', () => {
    const list = [
      { id: 1, status: 'online', enabled: true, maintenance: true, latency_ms: 1 },
      { id: 2, status: 'online', enabled: true, maintenance: false, latency_ms: 1 },
    ] as unknown as HostListItem[];
    const next = patchHostListV1(list, new Map([[1, ping(1, false)], [2, ping(2, false)]]))!;
    expect(next[0].status).toBe('maintenance');
    expect(next[1].status).toBe('offline');
  });
});

describe('applyLiveUpdates', () => {
  it('patches cached entries and leaves dataUpdatedAt alone', () => {
    const qc = new QueryClient();
    const list = [{ id: 1, online: true, latency_ms: 1, last_seen: null }] as unknown as HostStatus[];
    qc.setQueryData(['hosts'], list, { updatedAt: 1000 });
    applyLiveUpdates(qc, new Map([[1, ping(1, false)]]), new Map());
    const d = qc.getQueryData<HostStatus[]>(['hosts'])!;
    expect(d[0].online).toBe(false);
    expect(qc.getQueryState(['hosts'])!.dataUpdatedAt).toBe(1000);
    // Absent entries are not created
    expect(qc.getQueryData(['hosts-v1'])).toBeUndefined();
  });

  it('refetches the v2 dashboard when a host flips, not on every ping', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyLiveUpdates(qc, new Map([[77, ping(77, true)]]), new Map());
    applyLiveUpdates(qc, new Map([[77, ping(77, true)]]), new Map());
    expect(spy).not.toHaveBeenCalled();
    applyLiveUpdates(qc, new Map([[77, ping(77, false)]]), new Map());
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([['dashboard-v2'], ['summary-v2']]);
  });
});

describe('hasStateFlip', () => {
  const list = [
    { id: 1, state: 'up' },
    { id: 2, state: 'unknown' },
    { id: 3, state: 'maintenance' },
  ] as unknown as HostListItem[];

  it('flags results that contradict the unified state', () => {
    expect(hasStateFlip(list, new Map([[1, ping(1, false)]]))).toBe(true);
    expect(hasStateFlip(list, new Map([[2, ping(2, true)]]))).toBe(true);
  });

  it('ignores agreeing results and maintenance hosts', () => {
    expect(hasStateFlip(list, new Map([[1, ping(1, true)]]))).toBe(false);
    expect(hasStateFlip(list, new Map([[3, ping(3, false)]]))).toBe(false);
  });
});
