'use client';

import { Badge, type BadgeTone } from '@/components/ui/Badge';
import type { ChartTokens } from '@/lib/chart-theme';
import { cn } from '@/lib/utils';

/**
 * Syslog severity (RFC 5424, 0 = emergency … 7 = debug) on the design-system
 * vocabulary. Deliberately not a rainbow: error or worse reads as "down",
 * warning as "warning", everything below is neutral information.
 */
export const SEVERITY_LABELS: Record<number, string> = {
  0: 'Emergency',
  1: 'Alert',
  2: 'Critical',
  3: 'Error',
  4: 'Warning',
  5: 'Notice',
  6: 'Info',
  7: 'Debug',
};

export const SEVERITY_SHORT: Record<number, string> = {
  0: 'EMERG',
  1: 'ALERT',
  2: 'CRIT',
  3: 'ERR',
  4: 'WARN',
  5: 'NOTICE',
  6: 'INFO',
  7: 'DEBUG',
};

export type SeverityLevel = 'error' | 'warning' | 'info' | 'debug';

export function severityLevel(sev: number | null | undefined): SeverityLevel {
  if (sev === null || sev === undefined || Number.isNaN(sev)) return 'info';
  const s = Math.round(sev);
  if (s <= 3) return 'error';
  if (s === 4) return 'warning';
  if (s === 7) return 'debug';
  return 'info';
}

const BADGE_TONE: Record<SeverityLevel, BadgeTone> = {
  error: 'down',
  warning: 'warning',
  info: 'neutral',
  debug: 'neutral',
};

/** Severity label as a small badge (error = down tone, warning = warning tone, rest neutral). */
export function SeverityBadge({ severity, short, className }: { severity: number; short?: boolean; className?: string }) {
  const level = severityLevel(severity);
  const label = (short ? SEVERITY_SHORT : SEVERITY_LABELS)[severity] ?? `Sev ${severity}`;
  return (
    <Badge
      tone={BADGE_TONE[level]}
      className={cn('justify-center font-mono', short ? 'min-w-[52px]' : 'min-w-[72px]', level === 'debug' && 'text-fg-3', className)}
      title={`Severity ${severity} · ${SEVERITY_LABELS[severity] ?? 'unknown'}`}
    >
      {label}
    </Badge>
  );
}

/** Chart colour for a severity: status colours for error/warning, greys otherwise. */
export function severityChartColor(sev: number, t: ChartTokens): string {
  switch (severityLevel(sev)) {
    case 'error': return t.status.down;
    case 'warning': return t.status.warning;
    case 'debug': return t.line;
    default: return t.text3;
  }
}

/**
 * Options for the "max severity" filter. The API returns messages with
 * severity <= n, so the labels say "or worse" instead of naming one level.
 */
export const SEVERITY_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All severities' },
  { value: '0', label: 'Emergency only' },
  { value: '1', label: 'Alert or worse' },
  { value: '2', label: 'Critical or worse' },
  { value: '3', label: 'Error or worse' },
  { value: '4', label: 'Warning or worse' },
  { value: '5', label: 'Notice or worse' },
  { value: '6', label: 'Info or worse' },
  { value: '7', label: 'Debug or worse' },
];

/**
 * Timestamps from the API are ISO strings; the live stream sends a
 * pre-formatted local "MM-DD HH:MM:SS". Never render "Invalid Date".
 */
export function formatLogTime(ts: string): { short: string; full: string } {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return { short: ts, full: ts };
  return { short: d.toLocaleTimeString(), full: d.toLocaleString() };
}

/** Link target for a log's host: the host detail when the id is known. */
export function hostHref(hostId: number | null | undefined, hostname: string): string {
  return hostId ? `/hosts/${hostId}` : `/hosts?q=${encodeURIComponent(hostname)}`;
}
