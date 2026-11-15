import { describe, expect, it } from 'vitest';
import type { DashboardIncident, DashboardV2, HostGroup, TopologySection } from '@/types/dashboard';
import { groupSummary, heroCounts, hourlySyslog, incidentCause, needItems, rankGroups } from './overview';

const NOW = Date.parse('2026-10-10T14:32:00Z');

function inc(partial: Partial<DashboardIncident>): DashboardIncident {
  return {
    id: 1, title: 't', severity: 'critical', rule: 'r', status: 'open', acknowledged: false, acknowledged_by: null,
    opened_at: null, age_seconds: 60, host_count: null, host_ids: null, summary: null, ...partial,
  };
}

const TOPO: Pick<TopologySection, 'parents' | 'roots'> = {
  roots: [{ id: 1, name: 'fw-zh-01', state: 'up', is_gateway: true, descendant_count: 40, worst_state: 'down' }],
  parents: [
    { id: 2, name: 'SW-ZH-CORE-02', state: 'degraded', state_reason: null, parent_id: 1, child_count: 8, descendant_count: 8,
      descendant_states: {}, worst_state: 'down', affected: true, link_provenance: {}, is_gateway: false,
      children: [
        { id: 10, name: 'SRV-APP-01', state: 'down', state_reason: null, provenance: 'unifi', child_count: 0 },
        { id: 11, name: 'PRN-ZH-02', state: 'down', state_reason: null, provenance: 'unifi', child_count: 0 },
        { id: 12, name: 'SRV-DC-01', state: 'degraded', state_reason: null, provenance: 'unifi', child_count: 0 },
        { id: 13, name: 'web-03', state: 'up', state_reason: null, provenance: 'unifi', child_count: 0 },
      ] },
    { id: 3, name: 'SW-ZH-CORE-01', state: 'up', state_reason: null, parent_id: 1, child_count: 20, descendant_count: 20,
      descendant_states: {}, worst_state: 'warning', affected: false, link_provenance: {}, is_gateway: false,
      children: [{ id: 20, name: 'web-01', state: 'warning', state_reason: null, provenance: 'proxmox', child_count: 0 }] },
  ],
};

describe('heroCounts', () => {
  it('splits hosts honestly and leaves disabled ones out', () => {
    expect(heroCounts({ counts: { up: 28, degraded: 6, warning: 2, down: 2, unknown: 9, maintenance: 1, disabled: 3 } }))
      .toEqual({ total: 48, healthy: 28, issues: 10, noData: 9, maintenance: 1 });
  });
});

describe('incidentCause', () => {
  it('finds the parent the hosts share, with its ancestors, and their states', () => {
    const c = incidentCause(inc({ host_ids: [10, 11, 12] }), TOPO);
    expect(c?.chain.map((n) => n.name)).toEqual(['fw-zh-01', 'SW-ZH-CORE-02']);
    expect(c?.shared).toBe(3);
    expect(c?.byState).toEqual({ down: 2, degraded: 1 });
  });
  it('gives nothing without recorded hosts or topology', () => {
    expect(incidentCause(inc({ host_ids: null }), TOPO)).toBeNull();
    expect(incidentCause(inc({ host_ids: [10, 11] }), null)).toBeNull();
  });
  it('needs at least two hosts or half of them below one parent', () => {
    expect(incidentCause(inc({ host_ids: [10, 30, 31, 32] }), TOPO)).toBeNull();
    expect(incidentCause(inc({ host_ids: [20] }), TOPO)?.chain.at(-1)?.name).toBe('SW-ZH-CORE-01');
  });
  it('survives a parent loop', () => {
    const loop = { roots: [], parents: TOPO.parents.map((p) => (p.id === 2 ? { ...p, parent_id: 2 } : p)) };
    expect(incidentCause(inc({ host_ids: [10, 11] }), loop)?.chain.map((n) => n.id)).toEqual([2]);
  });
});

function dash(partial: Partial<DashboardV2>): Pick<DashboardV2, 'groups' | 'health' | 'incidents' | 'upcoming'> {
  return { groups: null, health: null, incidents: null, upcoming: null, ...partial };
}

const probe = (p: Partial<HostGroup>): HostGroup => ({
  kind: 'probe', id: 7, name: 'probe-bern-01', description: 'Bern', host_count: 9, by_state: { unknown: 9 }, fresh: false,
  last_report: '2026-10-10T14:21:00Z', ...p,
});

describe('needItems', () => {
  it('folds a silent probe into one row and puts it before warnings', () => {
    const items = needItems(dash({
      groups: [probe({})],
      upcoming: { agent_disk_predictions_ready: true, items: [
        { kind: 'certificate', due_at: null, days: 9, title: 'TLS', estimated_due: true, object: { kind: 'host', id: 4, name: 'portal' }, source: 'ssl' },
      ] },
    }), { discovery: 2 }, NOW);
    expect(items.map((i) => i.key)).toEqual(['probe-7', 'cert', 'discovery']);
    expect(items[0].title).toBe('probe-bern-01 silent for 11 min');
    expect(items[0].detail).toContain('9 hosts without data');
    expect(items[2].title).toBe('2 new hosts found');
  });

  it('counts hosts with issues outside every incident', () => {
    const health = {
      counts: { up: 28, degraded: 6, warning: 2, down: 2, unknown: 9, maintenance: 1, disabled: 0 },
      reasons: { warning: [{ text: 'Port 443 check failed', count: 1 }] },
      worst: 'down' as const,
      totals: { hosts: 48, disabled: 0, with_current_data: 39, agents: 0, agents_reporting: 0, integrations: 0, integrations_error: 0, probes: 1, probes_stale: 1 },
    };
    const incidents = {
      counts: { open: 2, acknowledged: 0, unacknowledged: 2, critical: 1, warning: 1, info: 0 },
      items: [inc({ id: 1, host_ids: [1, 2, 3, 4, 5, 6, 7, 8] }), inc({ id: 2, host_ids: [9] })],
      per_day: [], resolved_today: [], day_starts_at: null,
    };
    const items = needItems(dash({ health, incidents }), undefined, NOW);
    expect(items[0]).toMatchObject({ key: 'hosts-outside', title: '1 host with issues outside every incident', href: '/hosts?state=down' });
  });

  it('does not guess when an incident did not record its hosts', () => {
    const health = {
      counts: { up: 1, degraded: 0, warning: 3, down: 0, unknown: 0, maintenance: 0, disabled: 0 }, reasons: {}, worst: 'warning' as const,
      totals: { hosts: 4, disabled: 0, with_current_data: 4, agents: 0, agents_reporting: 0, integrations: 0, integrations_error: 0, probes: 0, probes_stale: 0 },
    };
    const incidents = { counts: { open: 1, acknowledged: 0, unacknowledged: 1, critical: 0, warning: 1, info: 0 }, items: [inc({ host_ids: null })], per_day: [], resolved_today: [], day_starts_at: null };
    expect(needItems(dash({ health, incidents }), undefined, NOW)).toEqual([]);
  });

  it('aggregates several disk forecasts into one row', () => {
    const disk = (days: number, title: string) => ({
      kind: 'disk' as const, due_at: null, days, title, estimated_due: true as const, method: 'linear', object: { kind: 'host', id: 1, name: 'x' },
      current_pct: 90, trend_pct_per_day: 1, confidence: 0.9, source: 'agent',
    });
    const items = needItems(dash({ upcoming: { agent_disk_predictions_ready: true, items: [disk(5, 'D: on SRV-FILE-01'), disk(2.6, 'L: on sql-07')] } }), undefined, NOW);
    expect(items).toHaveLength(1);
    expect(items[0].title).toBe('2 volumes full within 5 days');
    expect(items[0].detail).toBe('Earliest: L: on sql-07 in ~3 days');
  });
});

describe('rankGroups / groupSummary', () => {
  it('puts silent probes and down hosts first and drops empty unused probes', () => {
    const direct: HostGroup = { kind: 'direct', id: null, name: 'Direct', description: 'core', host_count: 37, by_state: { up: 26, down: 2, degraded: 6, warning: 2, maintenance: 1 }, fresh: null };
    const quiet = probe({ id: 8, name: 'probe-lu-01', host_count: 0, by_state: {}, fresh: true });
    const order = rankGroups([direct, quiet, probe({})]).map((g) => g.name);
    expect(order).toEqual(['probe-bern-01', 'Direct']);
    expect(groupSummary(direct)).toBe('26 healthy · 10 issues · 1 maint.');
    expect(groupSummary(probe({}))).toBe('no data · probe silent');
  });
});

describe('hourlySyslog', () => {
  it('averages 15-minute buckets per hour, as messages per minute', () => {
    const b = (count: number) => ({ t: '', count, errors: 0 });
    expect(hourlySyslog({ bucket_minutes: 15, buckets: [b(150), b(150), b(150), b(150), b(690), b(690)] })).toEqual([10, 46]);
  });
});
