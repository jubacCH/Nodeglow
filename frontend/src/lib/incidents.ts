/**
 * Incident list helpers: filter state <-> URL, query string for
 * GET /api/v1/incidents (docs/design/05-dashboard-api.md §3), dates and the
 * mapping of severities and host states onto the status vocabulary.
 */
import type { Incident } from '@/types';
import { toHealthState, type HealthState } from './status';

export type IncidentStatus = Incident['status'];
export type IncidentSeverity = Incident['severity'];
export type IncidentSort = 'updated' | 'created' | 'severity';
export type TimeRange = '24h' | '7d' | '30d' | 'all';

export const INCIDENT_STATUSES: IncidentStatus[] = ['open', 'acknowledged', 'resolved'];
export const INCIDENT_SEVERITIES: IncidentSeverity[] = ['critical', 'warning', 'info'];
export const DEFAULT_STATUSES: IncidentStatus[] = ['open', 'acknowledged'];
export const DEFAULT_SORT: IncidentSort = 'severity';
export const PAGE_SIZE = 25;

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  open: 'Open',
  acknowledged: 'Acknowledged',
  resolved: 'Resolved',
};

export const SEVERITY_LABEL: Record<IncidentSeverity, string> = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
};

export const TIME_RANGES: { value: TimeRange; label: string; hours: number | null }[] = [
  { value: '24h', label: 'Last 24 hours', hours: 24 },
  { value: '7d', label: 'Last 7 days', hours: 24 * 7 },
  { value: '30d', label: 'Last 30 days', hours: 24 * 30 },
  { value: 'all', label: 'Any time', hours: null },
];

/** Correlation rules the backend opens incidents for (services/*). Alert
 *  rules appear as `alert_rule_<id>`; anything seen in the data is added. */
export const KNOWN_RULES = [
  'agent_service', 'content_anomaly', 'disk_space', 'fleet_wide_issue', 'host_down_syslog',
  'integration_host', 'log_anomaly', 'multi_host_down', 'port_error', 'self_check',
  'severity_trend', 'syslog_spike', 'upstream_failure',
];

/** Compact host entry attached to an incident, with its current state. */
export interface IncidentHost {
  id: number;
  name: string | null;
  hostname: string | null;
  /** Unified host state (up, down, warning, degraded, maintenance, disabled, unknown); null = host deleted. */
  state: string | null;
  state_reason: string | null;
}

/** Incident as the v1 list/detail returns it since migration 037. */
export interface IncidentItem extends Incident {
  acknowledged?: boolean;
  /** null = hosts were not recorded (older incidents, rules without a host). */
  host_ids?: number[] | null;
  host_count?: number | null;
  hosts?: IncidentHost[] | null;
}

export interface IncidentPage {
  items: IncidentItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface IncidentFilters {
  statuses: IncidentStatus[];
  severities: IncidentSeverity[];
  rule: string;
  range: TimeRange;
  search: string;
  sort: IncidentSort;
  /** 0-based page. */
  page: number;
}

function csv<T extends string>(raw: string | null, allowed: readonly T[]): T[] {
  if (!raw) return [];
  const set = new Set(raw.split(',').map((s) => s.trim()));
  return allowed.filter((a) => set.has(a));
}

/** Filters from the URL. `?tab=incidents` (old "all incidents" tab) means every status. */
export function filtersFromParams(p: URLSearchParams): IncidentFilters {
  const rawStatus = p.get('status');
  let statuses: IncidentStatus[];
  if (rawStatus === 'all' || (rawStatus === null && p.get('tab') === 'incidents')) statuses = [...INCIDENT_STATUSES];
  else if (rawStatus === null) statuses = [...DEFAULT_STATUSES];
  else statuses = csv(rawStatus, INCIDENT_STATUSES);
  if (statuses.length === 0) statuses = [...DEFAULT_STATUSES];
  const range = (TIME_RANGES.find((r) => r.value === p.get('range'))?.value ?? 'all') as TimeRange;
  const sortRaw = p.get('sort');
  const sort: IncidentSort = sortRaw === 'updated' || sortRaw === 'created' || sortRaw === 'severity' ? sortRaw : DEFAULT_SORT;
  const page = Math.max(0, (Number.parseInt(p.get('page') ?? '1', 10) || 1) - 1);
  return {
    statuses,
    severities: csv(p.get('severity'), INCIDENT_SEVERITIES),
    rule: p.get('rule') ?? '',
    range,
    search: p.get('q') ?? '',
    sort,
    page,
  };
}

function sameSet<T>(a: T[], b: T[]) {
  return a.length === b.length && a.every((x) => b.includes(x));
}

/** URL query for the filters; defaults are left out so /alerts stays clean. */
export function filtersToParams(f: IncidentFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (!sameSet(f.statuses, DEFAULT_STATUSES)) {
    p.set('status', sameSet(f.statuses, INCIDENT_STATUSES) ? 'all' : f.statuses.join(','));
  }
  if (f.severities.length && f.severities.length < INCIDENT_SEVERITIES.length) p.set('severity', f.severities.join(','));
  if (f.rule) p.set('rule', f.rule);
  if (f.range !== 'all') p.set('range', f.range);
  if (f.search.trim()) p.set('q', f.search.trim());
  if (f.sort !== DEFAULT_SORT) p.set('sort', f.sort);
  if (f.page > 0) p.set('page', String(f.page + 1));
  return p;
}

export function hasActiveFilters(f: IncidentFilters): boolean {
  return !sameSet(f.statuses, DEFAULT_STATUSES) || f.severities.length > 0 || !!f.rule || f.range !== 'all' || !!f.search.trim();
}

/**
 * Query string for GET /api/v1/incidents?envelope=true. `now` pins the time
 * range to a stable boundary (rounded down to the minute) so the query key
 * does not change on every render.
 */
export function incidentQueryString(f: IncidentFilters, now: number = Date.now(), pageSize = PAGE_SIZE): string {
  const p = new URLSearchParams();
  p.set('envelope', 'true');
  p.set('status', sameSet(f.statuses, INCIDENT_STATUSES) ? 'all' : f.statuses.join(','));
  if (f.severities.length && f.severities.length < INCIDENT_SEVERITIES.length) p.set('severity', f.severities.join(','));
  if (f.rule) p.set('rule', f.rule);
  const hours = TIME_RANGES.find((r) => r.value === f.range)?.hours ?? null;
  if (hours !== null) {
    const minute = Math.floor(now / 60_000) * 60_000;
    p.set('from', new Date(minute - hours * 3_600_000).toISOString());
  }
  if (f.search.trim()) p.set('search', f.search.trim());
  p.set('sort', f.sort);
  p.set('limit', String(pageSize));
  p.set('offset', String(f.page * pageSize));
  return p.toString();
}

/**
 * The backend writes naive UTC timestamps ("2026-10-10T08:12:03" or
 * "2026-10-10 08:12:03.5"). Without a zone, `new Date()` would read them as
 * local time, so a missing offset is treated as UTC.
 */
export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  let s = value.trim().replace(' ', 'T');
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) s += 'Z';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "45s", "12m", "3h 20m", "2d 4h". */
export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

/** "5m ago" relative to now. */
export function formatAgo(value: string | null | undefined, now: number = Date.now()): string {
  const d = parseServerDate(value);
  if (!d) return '—';
  const diff = now - d.getTime();
  if (diff < 60_000) return 'just now';
  return `${formatDuration(diff)} ago`;
}

/** Absolute local date + time, for titles and detail facts. */
export function formatDateTime(value: string | null | undefined): string {
  const d = parseServerDate(value);
  if (!d) return '—';
  return d.toLocaleString([], { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** How long the incident has been (or was) open. */
export function incidentDurationMs(inc: Pick<Incident, 'created_at' | 'resolved_at'>, now: number = Date.now()): number | null {
  const start = parseServerDate(inc.created_at);
  if (!start) return null;
  const end = parseServerDate(inc.resolved_at)?.getTime() ?? now;
  return Math.max(0, end - start.getTime());
}

/** Severity as a health state for dots; info has no status colour (null). */
export function severityState(severity: IncidentSeverity): HealthState | null {
  if (severity === 'critical') return 'down';
  if (severity === 'warning') return 'warning';
  return null;
}

/** Glow rule: only critical, unacknowledged, unresolved incidents breathe. */
export function incidentNeedsGlow(inc: Pick<Incident, 'severity' | 'status'>): boolean {
  return inc.severity === 'critical' && inc.status === 'open';
}

/** Host state from the API onto the status vocabulary; deleted host = unknown. */
export function hostHealth(state: string | null | undefined): HealthState | 'disabled' {
  if (state === 'disabled') return 'disabled';
  return toHealthState(state);
}

const HOST_STATE_LABEL: Record<string, string> = {
  up: 'Up', down: 'Down', warning: 'Warning', degraded: 'Degraded',
  maintenance: 'Maintenance', disabled: 'Disabled', unknown: 'No data',
};

export function hostStateLabel(state: string | null | undefined): string {
  if (!state) return 'No data';
  return HOST_STATE_LABEL[state] ?? state;
}

/** Display name for an incident host; deleted hosts keep their id. */
export function hostLabel(h: IncidentHost): string {
  return h.name || h.hostname || `Host #${h.id}`;
}

/** "multi_host_down" -> "Multi host down"; alert_rule_7 -> "Alert rule 7". */
export function ruleLabel(rule: string): string {
  const s = rule.replace(/_/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : rule;
}
