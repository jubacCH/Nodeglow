import { describe, expect, it } from 'vitest';
import {
  filtersFromParams, filtersToParams, formatDuration, hasActiveFilters, hostHealth,
  incidentDurationMs, incidentNeedsGlow, incidentQueryString, parseServerDate, severityState,
} from './incidents';

describe('incident filters', () => {
  it('defaults to open + acknowledged, sorted by severity', () => {
    const f = filtersFromParams(new URLSearchParams(''));
    expect(f.statuses).toEqual(['open', 'acknowledged']);
    expect(f.sort).toBe('severity');
    expect(f.page).toBe(0);
    expect(hasActiveFilters(f)).toBe(false);
    expect(filtersToParams(f).toString()).toBe('');
  });

  it('keeps the old ?tab=incidents link meaning "all statuses"', () => {
    const f = filtersFromParams(new URLSearchParams('tab=incidents'));
    expect(f.statuses).toEqual(['open', 'acknowledged', 'resolved']);
    expect(filtersToParams(f).get('status')).toBe('all');
  });

  it('round-trips filters through the URL', () => {
    const p = new URLSearchParams('status=resolved&severity=critical,warning&rule=port_error&range=7d&q=db&sort=created&page=3');
    const f = filtersFromParams(p);
    expect(f).toMatchObject({
      statuses: ['resolved'], severities: ['critical', 'warning'], rule: 'port_error',
      range: '7d', search: 'db', sort: 'created', page: 2,
    });
    expect(filtersFromParams(filtersToParams(f))).toEqual(f);
  });

  it('ignores unknown values', () => {
    const f = filtersFromParams(new URLSearchParams('status=bogus&severity=nope&sort=x&range=1y&page=-4'));
    expect(f.statuses).toEqual(['open', 'acknowledged']);
    expect(f.severities).toEqual([]);
    expect(f.sort).toBe('severity');
    expect(f.range).toBe('all');
    expect(f.page).toBe(0);
  });

  it('builds the API query with envelope, offset and a minute-stable from', () => {
    const f = filtersFromParams(new URLSearchParams('range=24h&page=2&severity=critical'));
    const now = Date.UTC(2026, 9, 10, 12, 30, 45);
    const q = new URLSearchParams(incidentQueryString(f, now));
    expect(q.get('envelope')).toBe('true');
    expect(q.get('status')).toBe('open,acknowledged');
    expect(q.get('severity')).toBe('critical');
    expect(q.get('offset')).toBe('25');
    expect(q.get('limit')).toBe('25');
    expect(q.get('from')).toBe('2026-10-09T12:30:00.000Z');
    expect(incidentQueryString(f, now + 10_000)).toBe(incidentQueryString(f, now));
  });
});

describe('incident dates', () => {
  it('reads naive server timestamps as UTC', () => {
    expect(parseServerDate('2026-10-10T08:00:00')?.toISOString()).toBe('2026-10-10T08:00:00.000Z');
    expect(parseServerDate('2026-10-10 08:00:00.250')?.toISOString()).toBe('2026-10-10T08:00:00.250Z');
    expect(parseServerDate('2026-10-10T08:00:00+02:00')?.toISOString()).toBe('2026-10-10T06:00:00.000Z');
    expect(parseServerDate(null)).toBeNull();
    expect(parseServerDate('garbage')).toBeNull();
  });

  it('formats durations', () => {
    expect(formatDuration(42_000)).toBe('42s');
    expect(formatDuration(12 * 60_000)).toBe('12m');
    expect(formatDuration(200 * 60_000)).toBe('3h 20m');
    expect(formatDuration(52 * 3_600_000)).toBe('2d 4h');
  });

  it('measures open incidents until now and resolved ones until resolution', () => {
    const now = Date.UTC(2026, 9, 10, 12, 0, 0);
    expect(incidentDurationMs({ created_at: '2026-10-10T11:00:00', resolved_at: null }, now)).toBe(3_600_000);
    expect(incidentDurationMs({ created_at: '2026-10-10T11:00:00', resolved_at: '2026-10-10T11:30:00' }, now)).toBe(1_800_000);
  });
});

describe('incident states', () => {
  it('glows only for critical, unacknowledged incidents', () => {
    expect(incidentNeedsGlow({ severity: 'critical', status: 'open' })).toBe(true);
    expect(incidentNeedsGlow({ severity: 'critical', status: 'acknowledged' })).toBe(false);
    expect(incidentNeedsGlow({ severity: 'warning', status: 'open' })).toBe(false);
  });

  it('maps severities and host states', () => {
    expect(severityState('critical')).toBe('down');
    expect(severityState('info')).toBeNull();
    expect(hostHealth('up')).toBe('ok');
    expect(hostHealth('maintenance')).toBe('maint');
    expect(hostHealth('disabled')).toBe('disabled');
    expect(hostHealth(null)).toBe('unknown');
  });
});
