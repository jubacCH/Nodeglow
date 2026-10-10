import { describe, expect, it } from 'vitest';
import { durationLabel, scheduleLabel, utcToZonedInput, weekdaysLabel } from './maintenance';

describe('durationLabel', () => {
  it('formats minutes', () => {
    expect(durationLabel(45)).toBe('45m');
    expect(durationLabel(150)).toBe('2h 30m');
    expect(durationLabel(120)).toBe('2h');
    expect(durationLabel(3 * 1440 + 60)).toBe('3d 1h');
    expect(durationLabel(0)).toBe('0m');
  });
});

describe('weekdaysLabel', () => {
  it('collapses runs of three or more', () => {
    expect(weekdaysLabel([0, 1, 2, 3, 4])).toBe('Mon–Fri');
    expect(weekdaysLabel([5, 6])).toBe('Sat, Sun');
    expect(weekdaysLabel([0, 2, 4])).toBe('Mon, Wed, Fri');
    expect(weekdaysLabel([0, 1, 2, 3, 4, 5, 6])).toBe('Every day');
  });
});

describe('utcToZonedInput', () => {
  it('shows the wall clock of the window zone', () => {
    expect(utcToZonedInput('2026-07-01T08:00:00Z', 'Europe/Zurich')).toBe('2026-07-01T10:00');
    expect(utcToZonedInput('2026-01-01T08:00:00Z', 'Europe/Zurich')).toBe('2026-01-01T09:00');
    expect(utcToZonedInput('2026-01-01T23:30:00Z', 'UTC')).toBe('2026-01-01T23:30');
  });

  it('is empty for missing or invalid input', () => {
    expect(utcToZonedInput(null, 'UTC')).toBe('');
    expect(utcToZonedInput('nope', 'UTC')).toBe('');
    expect(utcToZonedInput('2026-01-01T00:00:00Z', 'Not/AZone')).toBe('');
  });
});

describe('scheduleLabel', () => {
  it('describes weekly windows', () => {
    expect(scheduleLabel({
      kind: 'weekly', weekdays: [6], start_time: '02:00', duration_minutes: 120,
      starts_at: null, ends_at: null, timezone: 'Europe/Zurich',
    })).toBe('Sun 02:00 for 2h (Europe/Zurich)');
  });
});
