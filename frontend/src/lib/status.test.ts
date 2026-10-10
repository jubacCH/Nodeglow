import { describe, expect, it } from 'vitest';
import { STATE_FILL, describeSegments, glows, toHealthState } from './status';

describe('status vocabulary', () => {
  it('maps legacy names onto design-system states', () => {
    expect(toHealthState('online')).toBe('ok');
    expect(toHealthState('offline')).toBe('down');
    expect(toHealthState('error')).toBe('warning');
    expect(toHealthState('maintenance')).toBe('maint');
    expect(toHealthState('disabled')).toBe('unknown');
    expect(toHealthState(null)).toBe('unknown');
  });

  it('lets only critical and warning glow', () => {
    expect(glows('down')).toBe('crit');
    expect(glows('warning')).toBe('warn');
    for (const s of ['ok', 'degraded', 'maint', 'unknown'] as const) expect(glows(s)).toBeNull();
  });

  it('never renders unknown as a solid colour', () => {
    expect(STATE_FILL.unknown).toBe('ng-hatch');
  });

  it('describes a health ring for screen readers', () => {
    expect(describeSegments([
      { state: 'ok', count: 29 }, { state: 'down', count: 2 }, { state: 'unknown', count: 9 }, { state: 'maint', count: 0 },
    ])).toBe('40 hosts: 29 ok, 2 down, 9 no data');
  });
});
