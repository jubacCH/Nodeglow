import { describe, expect, it } from 'vitest';
import type { DashboardIncident, DashboardV2, HostGroup, SiteGroup, TopologySection } from '@/types/dashboard';
import { groupSummary, heroCounts, hourlySyslog, incidentCause, needItems, rankGroups, rankSites, siteAsGroup, siteCheckers } from './overview';

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

  describe('link targets', () => {
    const disk = (days: number, object: { kind: string; id: number | null; name: string; type?: string }) => ({
      kind: 'disk' as const, due_at: null, days, title: `${object.name} · Pool 1 90.7 %`, estimated_due: true as const,
      method: 'linear trend over 14 days', object, current_pct: 90.7, trend_pct_per_day: 0.97, confidence: 0.9, source: null,
    });
    const nas = { kind: 'integration', id: 12, name: 'UniFi NAS Instance', type: 'unas' };
    const up = (items: ReturnType<typeof disk>[]) => ({ agent_disk_predictions_ready: true, items });
    const hrefOf = (d: Parameters<typeof needItems>[0], key: string) => needItems(d, undefined, NOW).find((i) => i.key === key)?.href;

    it('opens the integration of an integration pool forecast', () => {
      expect(hrefOf(dash({ upcoming: up([disk(10, nas)]) }), 'disk')).toBe('/integration/unas/12');
    });

    it('opens the agent of an agent disk forecast', () => {
      expect(hrefOf(dash({ upcoming: up([disk(5, { kind: 'agent', id: 3, name: 'SRV-FILE-01' })]) }), 'disk')).toBe('/agents/3');
    });

    it('opens the earliest object when several volumes fill up', () => {
      const d = dash({ upcoming: up([disk(20, { kind: 'agent', id: 3, name: 'SRV-FILE-01' }), disk(10, nas)]) });
      expect(hrefOf(d, 'disk')).toBe('/integration/unas/12');
    });

    it('opens the failing integration itself, not its type list', () => {
      const health = {
        counts: { up: 0, degraded: 0, warning: 0, down: 0, unknown: 0, maintenance: 0, disabled: 0 }, reasons: {}, worst: null,
        totals: { hosts: 0, disabled: 0, with_current_data: 0, agents: 0, agents_reporting: 0, integrations: 1, integrations_error: 1, probes: 0, probes_stale: 0 },
        failing_integrations: [{ id: 5, type: 'proxmox', name: 'pve', error: 'timeout', since_check: null }],
      };
      expect(hrefOf(dash({ health } as Parameters<typeof dash>[0]), 'integration-5')).toBe('/integration/proxmox/5');
    });

    it('opens the silent probe, or the agent list when the probe is gone', () => {
      expect(hrefOf(dash({ groups: [probe({})] }), 'probe-7')).toBe('/agents/7');
      expect(hrefOf(dash({ groups: [probe({ id: null })] }), 'probe-null')).toBe('/agents');
    });

    it('opens the host of a host certificate and the certificate list otherwise', () => {
      const cert = (object: { kind: string; id: number | null; name: string }) => ({
        kind: 'certificate' as const, due_at: null, days: 9, title: 'Certificate expires', estimated_due: true as const, object, source: 'x',
      });
      const certs = (items: ReturnType<typeof cert>[]) => dash({ upcoming: { agent_disk_predictions_ready: true, items } });
      expect(hrefOf(certs([cert({ kind: 'host', id: 4, name: 'portal' })]), 'cert')).toBe('/hosts/4');
      expect(hrefOf(certs([cert({ kind: 'certificate', id: null, name: 'npm' })]), 'cert')).toBe('/ssl');
    });
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

describe('sites on the overview', () => {
  const site = (over: Partial<SiteGroup>): SiteGroup => ({
    id: 1, name: 'Zürich HQ', is_default: true, host_count: 30, by_state: { up: 28, down: 2 }, worst: 'down', probes: [], ...over,
  });
  const bern = site({ id: 2, name: 'Bern', is_default: false, host_count: 9, by_state: { unknown: 9 }, worst: 'unknown',
    probes: [{ id: 7, name: 'probe-bern-01', fresh: false, host_count: 9 }] });

  it('ranks a site behind a silent probe first and drops empty sites', () => {
    const empty = site({ id: 4, name: 'Olten', is_default: false, host_count: 0, by_state: {}, worst: null });
    expect(rankSites([site({}), empty, bern]).map((s) => s.name)).toEqual(['Bern', 'Zürich HQ']);
  });

  it('describes a silent site as no data, never healthy', () => {
    expect(groupSummary(siteAsGroup(bern))).toBe('no data · probe silent');
    expect(groupSummary(siteAsGroup(site({})))).toBe('28 healthy · 2 issues');
    const mixed = site({ by_state: { up: 5, unknown: 4 }, host_count: 9, probes: [{ id: 7, name: 'p', fresh: false, host_count: 4 }] });
    expect(siteAsGroup(mixed).fresh).toBeNull();
  });

  it('names who checks the site', () => {
    expect(siteCheckers(bern)).toBe('via probe-bern-01');
    expect(siteCheckers(site({}))).toBe('default site');
    expect(siteCheckers(site({ is_default: false }))).toBe('no probe');
    expect(siteCheckers(site({ probes: [{ id: 1, name: 'a', fresh: true, host_count: 1 }, { id: 2, name: 'b', fresh: false, host_count: 1 }] })))
      .toBe('2 probes, 1 silent');
  });
});

describe('hourlySyslog', () => {
  it('averages 15-minute buckets per hour, as messages per minute', () => {
    const b = (count: number) => ({ t: '', count, errors: 0 });
    expect(hourlySyslog({ bucket_minutes: 15, buckets: [b(150), b(150), b(150), b(150), b(690), b(690)] })).toEqual([10, 46]);
  });
});
