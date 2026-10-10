import { describe, expect, it } from 'vitest';
import {
  availabilityScale, changeHref, changeTiles, describeLatency, describeStates, formatDuration,
  glyphFor, groupFreshness, healthSegments, hostState, incidentDayBars, incidentHostsLabel,
  isEmptyInstall, isStale, reachabilityFlipped, sectionError, speedBars, stateSegments,
  syslogLevel, topologyColumns, upcomingView, wanState,
} from './dashboard';
import type {
  ChangeItem, DashboardV2, HostGroup, LatencySection, TopoParent, TopologySection, UpcomingItem,
} from '@/types/dashboard';

const NOW = Date.parse('2026-10-10T14:32:00Z');

describe('hostState', () => {
  it('maps the unified states and never turns missing data into ok', () => {
    expect(hostState('up')).toBe('ok');
    expect(hostState('maintenance')).toBe('maint');
    expect(hostState('unknown')).toBe('unknown');
    expect(hostState('disabled')).toBe('unknown');
    expect(hostState(null)).toBe('unknown');
    expect(hostState(undefined)).toBe('unknown');
  });
});

describe('healthSegments', () => {
  it('orders states healthy → no data and attaches the top reasons', () => {
    const segs = healthSegments({
      counts: { up: 29, degraded: 5, warning: 2, down: 2, unknown: 9, maintenance: 1, disabled: 3 },
      reasons: {
        down: [{ text: 'behind SW-CORE-2', count: 2 }],
        unknown: [{ text: 'probe probe-a silent 11 min', count: 9 }],
      },
    });
    expect(segs.map((s) => [s.state, s.count])).toEqual([
      ['ok', 29], ['degraded', 5], ['warning', 2], ['down', 2], ['maint', 1], ['unknown', 9],
    ]);
    expect(segs.find((s) => s.state === 'down')?.detail).toBe('behind SW-CORE-2 ×2');
    // Disabled hosts are not part of the ring.
    expect(segs.reduce((a, s) => a + s.count, 0)).toBe(48);
  });
});

describe('state distributions', () => {
  it('drops empty states and describes the rest', () => {
    expect(stateSegments({ up: 3, unknown: 0, down: 1 }).map((s) => s.state)).toEqual(['ok', 'down']);
    expect(describeStates({ up: 27, degraded: 5, down: 2 })).toBe('27 up · 5 degraded · 2 down');
    expect(describeStates({})).toBe('No hosts');
  });
});

describe('formatting', () => {
  it('formats durations compactly', () => {
    expect(formatDuration(42)).toBe('42 s');
    expect(formatDuration(25 * 60)).toBe('25 min');
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe('3 h 5 min');
    expect(formatDuration(2 * 86400 + 4 * 3600)).toBe('2 d 4 h');
    expect(formatDuration(null)).toBe('—');
  });

  it('treats missing timestamps as stale', () => {
    expect(isStale(null, NOW, 60)).toBe(true);
    expect(isStale('2026-10-10T14:31:30Z', NOW, 60)).toBe(false);
    expect(isStale('2026-10-10T10:00:00Z', NOW, 3 * 3600)).toBe(true);
  });

  it('says "not recorded" instead of "no hosts" for null host counts', () => {
    expect(incidentHostsLabel(null)).toBe('affected hosts not recorded');
    expect(incidentHostsLabel(0)).toBe('no hosts affected');
    expect(incidentHostsLabel(1)).toBe('1 host affected');
    expect(incidentHostsLabel(7)).toBe('7 hosts affected');
  });
});

describe('sectionError', () => {
  it('finds the failed section', () => {
    const d = { errors: [{ section: 'syslog', error: 'TimeoutError' }] };
    expect(sectionError(d, 'syslog')?.error).toBe('TimeoutError');
    expect(sectionError(d, 'health')).toBeNull();
    expect(sectionError(undefined, 'health')).toBeNull();
  });
});

describe('incident and speed bars', () => {
  it('scales days to the max and marks today', () => {
    const bars = incidentDayBars([
      { date: '2026-10-08', count: 0, critical: 0, warning: 0, info: 0 },
      { date: '2026-10-09', count: 2, critical: 1, warning: 1, info: 0 },
      { date: '2026-10-10', count: 4, critical: 2, warning: 2, info: 0 },
    ]);
    expect(bars.map((b) => b.heightPct)).toEqual([6, 50, 100]);
    expect(bars[2].today).toBe(true);
    expect(bars[2].label).toMatch(/^Today: 4 incidents/);
  });

  it('keeps missing speed results visible as stubs', () => {
    const bars = speedBars([
      { at: 'a', download_mbps: 500, upload_mbps: 50, latency_ms: 5 },
      { at: 'b', download_mbps: null, upload_mbps: null, latency_ms: null },
      { at: 'c', download_mbps: 1000, upload_mbps: 50, latency_ms: 5 },
    ]);
    expect(bars.map((b) => b.heightPct)).toEqual([50, 3, 100]);
  });

  it('only shows a WAN state when UniFi reported one', () => {
    expect(wanState(null)).toBeNull();
    expect(wanState('up')).toBe('ok');
    expect(wanState('down')).toBe('down');
    expect(wanState('degraded')).toBe('unknown');
  });
});

describe('glyphFor', () => {
  it('guesses the device from the name', () => {
    expect(glyphFor('SW-CORE-02')).toBe('switch');
    expect(glyphFor('fw-hq-01')).toBe('firewall');
    expect(glyphFor('AP-OG3')).toBe('ap');
    expect(glyphFor('NAS-01')).toBe('nas');
    expect(glyphFor('pve2')).toBe('hyper');
    expect(glyphFor('PRN-02')).toBe('printer');
    expect(glyphFor('probe-branch')).toBe('probe');
    expect(glyphFor('app-01', { provenance: 'proxmox' })).toBe('vm');
    expect(glyphFor('SRV-APP-01')).toBe('server');
    expect(glyphFor('anything', { gateway: true })).toBe('firewall');
  });
});

function parent(id: number, worst: TopoParent['worst_state'], desc: number, extra: Partial<TopoParent> = {}): TopoParent {
  return {
    id, name: `p${id}`, state: 'up', state_reason: null, parent_id: 1, child_count: desc, descendant_count: desc,
    descendant_states: { up: desc }, worst_state: worst, affected: worst !== 'up' && worst !== 'maintenance',
    link_provenance: {}, is_gateway: false, ...extra,
  };
}

const probe = (fresh: boolean, hosts = 4): HostGroup => ({
  kind: 'probe', id: 9, name: 'probe-x', description: 'Checked by remote probe', host_count: hosts,
  by_state: fresh ? { up: hosts } : { unknown: hosts }, fresh, last_report: '2026-10-10T14:21:00Z', staleness_window_seconds: 180,
});

describe('topologyColumns', () => {
  const topo = (parents: TopoParent[]): TopologySection => ({
    roots: [], parents, parents_total: parents.length, unlinked_hosts: 0, links_by_provenance: {}, internet_root: null,
  });

  it('puts affected parents first, then silent probes, then the largest healthy ones', () => {
    const cols = topologyColumns(
      topo([parent(2, 'down', 7), parent(1, 'up', 40, { parent_id: null, is_gateway: true }), parent(3, 'up', 27), parent(4, 'up', 3)]),
      [probe(false)],
      [1],
    );
    expect(cols.map((c) => c.key)).toEqual(['p2', 'g9', 'p3']);
    expect(cols.map((c) => c.tone)).toEqual(['bad', 'unknown', 'ok']);
  });

  it('keeps a silent probe visible when affected parents fill the row', () => {
    const cols = topologyColumns(topo([parent(2, 'down', 1), parent(3, 'warning', 1), parent(4, 'degraded', 1)]), [probe(false)], []);
    expect(cols.map((c) => c.key)).toEqual(['p2', 'p3', 'g9']);
  });

  it('ignores fresh probes and falls back to the root when it is the only parent', () => {
    const cols = topologyColumns(topo([parent(1, 'up', 5, { parent_id: null })]), [probe(true)], [1]);
    expect(cols.map((c) => c.key)).toEqual(['p1']);
  });
});

describe('groupFreshness', () => {
  it('describes a silent probe with its age and threshold', () => {
    const f = groupFreshness(probe(false), NOW);
    expect(f.stale).toBe(true);
    expect(f.text).toContain('probe silent 11 min');
    expect(f.text).toContain('threshold 3 min');
  });

  it('flags hosts whose probe no longer exists', () => {
    expect(groupFreshness({ ...probe(false), id: null }, NOW).stale).toBe(true);
  });
});

describe('upcomingView', () => {
  it('shows when an active window ends', () => {
    const item: UpcomingItem = {
      kind: 'maintenance', phase: 'active', due_at: '2026-10-10T15:00:00Z', starts_at: '2026-10-10T14:00:00Z',
      ends_at: '2026-10-10T15:00:00Z', title: 'NAS firmware update', object: { kind: 'maintenance_window', id: 1, name: 'x' },
      scope: { all_hosts: false, host_ids: [3], host_names: ['NAS-01'] },
    };
    const v = upcomingView(item, NOW);
    expect(v.whenDetail).toBe('ends in 28 min');
    expect(v.subtitle).toContain('NAS-01');
  });

  it('labels disk forecasts as estimates with the fill level', () => {
    const v = upcomingView({
      kind: 'disk', due_at: '2026-10-15T14:32:00Z', days: 5.2, title: 'SRV-FILE · D: 91 %', estimated_due: true,
      method: 'linear trend over 14 days', object: { kind: 'agent', id: 4, name: 'SRV-FILE' }, current_pct: 91,
      trend_pct_per_day: 1.6, confidence: 0.8, source: 'agent',
    }, NOW);
    expect(v.when).toBe('~5 days');
    expect(v.progressPct).toBe(91);
    expect(v.href).toBe('/agents/4');
  });
});

describe('syslogLevel', () => {
  it('compares the current rate with the learned band', () => {
    const usual = { low_per_min: 8, high_per_min: 14, mean_per_min: 11, sources: 3, method: 'm' };
    expect(syslogLevel({ current_per_min: 46, usual })).toBe('above');
    expect(syslogLevel({ current_per_min: 10, usual })).toBe('within');
    expect(syslogLevel({ current_per_min: 2, usual })).toBe('below');
    expect(syslogLevel({ current_per_min: 46, usual: null })).toBeNull();
  });
});

describe('availabilityScale', () => {
  it('spans twice the target gap and positions value and target', () => {
    const s = availabilityScale({ pct: 99.94, target_pct: 99.9 });
    expect(s.lo).toBeCloseTo(99.8);
    expect(s.targetPct).toBeCloseTo(50);
    expect(s.fillPct).toBeCloseTo(70);
  });

  it('widens for values below the scale and has no fill without data', () => {
    const s = availabilityScale({ pct: 98, target_pct: 99.9 });
    expect(s.lo).toBeLessThan(98);
    expect(s.fillPct).toBeGreaterThan(0);
    expect(availabilityScale({ pct: null, target_pct: 99.9 }).fillPct).toBeNull();
  });
});

describe('changes', () => {
  const item = (type: ChangeItem['type'], title: string, extra: Partial<ChangeItem> = {}): ChangeItem => ({
    type, at: '2026-10-10T12:00:00Z', title, object: { kind: 'incident', id: 1, name: title }, detail: null, ...extra,
  });

  it('builds tiles in a fixed order with details and a remainder', () => {
    const { tiles, rest } = changeTiles(
      { incident_opened: 4, incident_resolved: 2, host_added: 2, maintenance_started: 1, agent_updated: 1, config_change: 3 },
      [
        item('incident_opened', '#1072 Service stopped'),
        item('incident_resolved', '#1069 HTTP 503', { detail: { duration_seconds: 720 } }),
        item('host_added', 'New host discovered: a', { object: { kind: 'host', id: 5, name: 'a' }, detail: { discovered: true } }),
        item('agent_updated', 'Agent x updated', { object: { kind: 'agent', id: 3, name: 'SRV-1' }, detail: { to: '0.4.2' } }),
      ],
    );
    expect(tiles.map((t) => t.type)).toEqual(['incident_opened', 'incident_resolved', 'host_added', 'maintenance_started', 'agent_updated']);
    expect(tiles[1].detail).toBe('#1069 HTTP 503 (12 min)');
    expect(tiles[2].label).toBe('New hosts discovered');
    expect(tiles[4].detail).toBe('SRV-1 → 0.4.2');
    expect(rest).toBe(3);
  });

  it('links changes to their object', () => {
    expect(changeHref(item('incident_opened', 'x'))).toBe('/incidents/1');
    expect(changeHref(item('host_added', 'x', { object: { kind: 'host', id: 5, name: 'a' } }))).toBe('/hosts/5');
    expect(changeHref(item('probe_silent', 'x', { object: { kind: 'probe', id: 9, name: 'p' } }))).toBe('/agents/9');
    expect(changeHref(item('config_change', 'x', { object: { kind: 'setting', id: null, name: null } }))).toBeNull();
  });
});

describe('describeLatency', () => {
  it('summarises the chart for screen readers', () => {
    const l: LatencySection = {
      incident_id: 1071, title: 'Uplink flapping', severity: 'critical', host_ids: [1, 2], host_names: ['a', 'b'],
      host_count: 2, onset_at: '2026-10-10T14:05:00Z', window_hours: 2, median_before_ms: 0.31, median_since_ms: 14.2,
      points: [
        { t: '2026-10-10T14:04:00Z', median_ms: 0.3, max_ms: 0.4, ok: 2, failed: 0 },
        { t: '2026-10-10T14:07:00Z', median_ms: 14.2, max_ms: 40, ok: 1, failed: 1 },
      ],
    };
    const text = describeLatency(l);
    expect(text).toContain('2 hosts of incident #1071');
    expect(text).toContain('Median 0.31 ms before the onset');
    expect(text).toContain('Highest 40 ms');
    expect(text).toContain('Failed checks from');
  });
});

describe('live refresh', () => {
  it('reports a flip only after a host was seen before', () => {
    const last = new Map<number, boolean>();
    expect(reachabilityFlipped(last, new Map([[1, { online: true }]]))).toBe(false);
    expect(reachabilityFlipped(last, new Map([[1, { online: true }]]))).toBe(false);
    expect(reachabilityFlipped(last, new Map([[1, { online: false }]]))).toBe(true);
  });
});

describe('isEmptyInstall', () => {
  it('needs real totals and nothing configured', () => {
    const totals = { hosts: 0, disabled: 0, with_current_data: 0, agents: 0, agents_reporting: 0, integrations: 0, integrations_error: 0, probes: 0, probes_stale: 0 };
    const d = (t: typeof totals) => ({ health: { totals: t } }) as unknown as DashboardV2;
    expect(isEmptyInstall(d(totals))).toBe(true);
    expect(isEmptyInstall(d({ ...totals, integrations: 1 }))).toBe(false);
    expect(isEmptyInstall({ health: null } as unknown as DashboardV2)).toBe(false);
  });
});
