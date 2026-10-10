import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  applyLiveUpdates,
  patchDashboard,
  patchHostListV1,
  patchHostStatusList,
  toUtcIso,
} from './liveUpdates';
import type { HostStatus, WsPingUpdate } from '../types';
import type { DashboardData, HostStat } from '../hooks/queries/useDashboard';
import type { HostListItem } from '../hooks/queries/useHosts';

function ping(host_id: number, online: boolean, latency_ms: number | null = 1): WsPingUpdate {
  return { type: 'ping_update', ts: '2026-10-10T12:00:00', host_id, name: `h${host_id}`, online, latency_ms };
}

function stat(id: number, online: boolean | null, maintenance = false): HostStat {
  return {
    host: { id, name: `h${id}`, hostname: `h${id}.lan`, source: 'manual', check_type: 'icmp', maintenance, port_error: false },
    online, latency: 1, sparkline: [], uptime_pct: 100, avg_latency: 1, health_score: 100,
  };
}

function dashboard(stats: HostStat[]): DashboardData {
  return { host_stats: stats, online_count: 0, offline_count: 0 } as unknown as DashboardData;
}

describe('toUtcIso', () => {
  it('marks zone-less backend timestamps as UTC', () => {
    expect(toUtcIso('2026-10-10T12:00:00')).toBe('2026-10-10T12:00:00Z');
    expect(toUtcIso('2026-10-10T12:00:00+02:00')).toBe('2026-10-10T12:00:00+02:00');
    expect(toUtcIso('2026-10-10T12:00:00Z')).toBe('2026-10-10T12:00:00Z');
  });
});

describe('patchDashboard', () => {
  it('updates status and recomputes counts without maintenance hosts', () => {
    const d = dashboard([stat(1, true), stat(2, true), stat(3, true, true)]);
    const next = patchDashboard(d, new Map([[1, ping(1, false)], [3, ping(3, false)]]))!;
    expect(next.host_stats[0].online).toBe(false);
    expect(next.offline_count).toBe(1);
    expect(next.online_count).toBe(1);
    // Untouched rows keep their identity
    expect(next.host_stats[1]).toBe(d.host_stats[1]);
  });

  it('returns the same object when nothing changed', () => {
    const d = dashboard([stat(1, true)]);
    expect(patchDashboard(d, new Map([[1, ping(1, true)]]))).toBe(d);
    expect(patchDashboard(d, new Map([[9, ping(9, false)]]))).toBe(d);
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
    qc.setQueryData(['dashboard'], dashboard([stat(1, true)]), { updatedAt: 1000 });
    applyLiveUpdates(qc, new Map([[1, ping(1, false)]]), new Map());
    const d = qc.getQueryData<DashboardData>(['dashboard'])!;
    expect(d.offline_count).toBe(1);
    expect(qc.getQueryState(['dashboard'])!.dataUpdatedAt).toBe(1000);
    // Absent entries are not created
    expect(qc.getQueryData(['hosts'])).toBeUndefined();
  });
});
