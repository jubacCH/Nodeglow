import type { MaintenanceWindow } from '@/types';

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

/** "2h 30m", "45m", "3d 2h" */
export function durationLabel(minutes: number | null | undefined): string {
  const m = Math.max(0, Math.round(minutes ?? 0));
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  const r = m % 60;
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (r || parts.length === 0) parts.push(`${r}m`);
  return parts.join(' ');
}

/** Weekday list as text, collapsing runs: [0,1,2,3,4] → "Mon–Fri". */
export function weekdaysLabel(days: number[]): string {
  const sorted = Array.from(new Set(days)).filter((d) => d >= 0 && d <= 6).sort((a, b) => a - b);
  if (sorted.length === 7) return 'Every day';
  const runs: string[] = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    runs.push(j - i >= 2 ? `${WEEKDAYS[sorted[i]]}–${WEEKDAYS[sorted[j]]}` : sorted.slice(i, j + 1).map((d) => WEEKDAYS[d]).join(', '));
    i = j + 1;
  }
  return runs.join(', ');
}

/**
 * Wall-clock "YYYY-MM-DDTHH:mm" of an instant in a time zone — the value a
 * datetime-local input shows. The backend reads such a value back as local
 * time in the window's zone, so editing round-trips exactly.
 */
export function utcToZonedInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date);
  } catch {
    return '';
  }
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00';
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}

/** Format an instant in the window's own time zone. */
export function formatInZone(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, {
      timeZone, dateStyle: 'medium', timeStyle: 'short',
    });
  } catch {
    return new Date(iso).toLocaleString();
  }
}

/** One-line description of when a window applies. */
export function scheduleLabel(w: Pick<MaintenanceWindow, 'kind' | 'weekdays' | 'start_time' | 'duration_minutes' | 'starts_at' | 'ends_at' | 'timezone'>): string {
  if (w.kind === 'once') {
    return `${formatInZone(w.starts_at, w.timezone)} → ${formatInZone(w.ends_at, w.timezone)} (${w.timezone})`;
  }
  return `${weekdaysLabel(w.weekdays)} ${w.start_time ?? ''} for ${durationLabel(w.duration_minutes)} (${w.timezone})`;
}

/** Time zones the browser knows, for the picker (empty if unsupported). */
export function knownTimeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    return intl.supportedValuesOf ? intl.supportedValuesOf('timeZone') : [];
  } catch {
    return [];
  }
}
