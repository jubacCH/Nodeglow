import { describe, expect, it } from 'vitest';
import {
  hostStateRank, hostStatusProp, normalizeHostState, observedText, reasonText, stateFromLegacyParam,
} from './hostState';
import { EMPTY_HOST_FORM, hasErrors, validateHostForm } from './hostForm';
import { bulkUpdatesFor, describeBulkResult } from './bulk';
import { withGaps } from './detail/latency';
import { parseCheckTypes } from './detail/checks';

describe('host state', () => {
  it('treats anything unexpected as unknown, never up', () => {
    expect(normalizeHostState(undefined)).toBe('unknown');
    expect(normalizeHostState('online')).toBe('unknown');
    expect(normalizeHostState('degraded')).toBe('degraded');
  });

  it('maps onto the design-system vocabulary', () => {
    expect(hostStatusProp('up')).toBe('ok');
    expect(hostStatusProp('maintenance')).toBe('maint');
    expect(hostStatusProp('disabled')).toBe('disabled');
    expect(hostStatusProp('unknown')).toBe('unknown');
  });

  it('sorts worst first like the backend', () => {
    const order = (['up', 'disabled', 'unknown', 'down', 'warning', 'maintenance', 'degraded'] as const)
      .slice().sort((a, b) => hostStateRank(a) - hostStateRank(b));
    expect(order).toEqual(['down', 'warning', 'degraded', 'unknown', 'maintenance', 'up', 'disabled']);
  });

  it('reads legacy ?status= links', () => {
    expect(stateFromLegacyParam('online')).toBe('up');
    expect(stateFromLegacyParam('offline')).toBe('down');
    expect(stateFromLegacyParam('error')).toBe('warning');
    expect(stateFromLegacyParam('all')).toBeNull();
    expect(stateFromLegacyParam('bogus')).toBeNull();
  });

  it('describes stale data as "no data since", never as observed', () => {
    const t = new Date(Date.now() - 12 * 60_000).toISOString();
    expect(observedText('unknown', t)).toMatch(/^No data since .* \(12m ago\)$/);
    expect(observedText('unknown', null)).toBe('Never observed');
    expect(observedText('up', t)).toBe('Observed 12m ago');
    expect(reasonText('unknown', null)).toMatch(/Not observed/);
    expect(reasonText('down', 'Not responding')).toBe('Not responding');
  });
});

describe('host form validation', () => {
  const ok = { ...EMPTY_HOST_FORM, name: 'Web', hostname: 'web.example.com' };

  it('accepts a minimal host', () => {
    expect(hasErrors(validateHostForm(ok, 'add'))).toBe(false);
  });

  it('requires name and hostname', () => {
    const e = validateHostForm(EMPTY_HOST_FORM, 'add');
    expect(e.name).toBeTruthy();
    expect(e.hostname).toBeTruthy();
  });

  it('rejects URLs, spaces and bad ports', () => {
    expect(validateHostForm({ ...ok, hostname: 'https://x.example' }, 'add').hostname).toMatch(/without http/);
    expect(validateHostForm({ ...ok, hostname: 'a b' }, 'add').hostname).toBeTruthy();
    expect(validateHostForm({ ...ok, port: '70000' }, 'add').port).toBeTruthy();
    expect(validateHostForm({ ...ok, check_type: 'tcp' }, 'add').port).toMatch(/needs a port/);
    expect(validateHostForm({ ...ok, hostname: '2001:db8::1' }, 'add').hostname).toBeUndefined();
  });

  it('validates the latency threshold in edit mode', () => {
    expect(validateHostForm({ ...ok, latency_threshold_ms: '0' }, 'edit').latency_threshold_ms).toBeTruthy();
    expect(validateHostForm({ ...ok, latency_threshold_ms: '250' }, 'edit').latency_threshold_ms).toBeUndefined();
  });
});

describe('bulk results', () => {
  it('builds the update payload', () => {
    expect(bulkUpdatesFor({ kind: 'maintenance', on: false })).toEqual({ maintenance: false });
    expect(bulkUpdatesFor({ kind: 'probe', probeId: null })).toEqual({ probe_id: null });
  });

  it('reports missing hosts and ignored fields', () => {
    const names = new Map([[7, 'nas']]);
    const r = describeBulkResult(
      { kind: 'enabled', on: false },
      { updated: 2, ids: [1, 2], missing: [7], ignored_fields: ['foo'] },
      names,
    );
    expect(r.tone).toBe('warning');
    expect(r.message).toBe('Monitoring disabled for 2 hosts.');
    expect(r.details[0]).toMatch(/1 host no longer exists .*nas/);
    expect(r.details[1]).toMatch(/ignored the field foo/);
  });

  it('flags a change that touched nothing', () => {
    const r = describeBulkResult({ kind: 'maintenance', on: true }, { updated: 0, ids: [], missing: [3], ignored_fields: [] });
    expect(r.tone).toBe('error');
  });
});

describe('latency gaps', () => {
  it('breaks the line where checks are missing', () => {
    const base = Date.parse('2026-10-10T10:00:00Z');
    const at = (min: number) => new Date(base + min * 60_000).toISOString();
    const pts = withGaps([0, 1, 2, 3, 20, 21].map((m) => ({ timestamp: at(m), success: true, latency_ms: 5 })));
    expect(pts.filter((p) => p.ms === null)).toHaveLength(1);
    expect(pts).toHaveLength(7);
  });
});

describe('check types', () => {
  it('parses tcp ports including the legacy form', () => {
    expect(parseCheckTypes('icmp,tcp:22,tcp:443', null).ports).toEqual([22, 443]);
    expect(parseCheckTypes('tcp', 8080).ports).toEqual([8080]);
    expect(parseCheckTypes('', null).types).toEqual(['icmp']);
  });
});
