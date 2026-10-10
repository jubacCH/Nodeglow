/**
 * Pure mapping helpers for the E3 dashboard (GET /api/v2/dashboard).
 *
 * Everything here turns API fields into what a card shows, and follows the
 * honesty rules of docs/design/05-dashboard-api.md: no data is never "up",
 * `null` stays "—" (never 0), and unknown never looks healthy. No React, no
 * DOM — unit-tested in dashboard.test.ts.
 */
import type { HealthSegment, HealthState } from './status';
import type {
  AvailabilitySection, ChangeItem, LatencySection, ChangeType, DashboardIncident, DashboardV2, HealthSection,
  HostGroup, HostStateName, IncidentDay, SectionError, SpeedPoint, StateCounts, SyslogSection,
  TopoParent, TopologySection, UpcomingItem,
} from '@/types/dashboard';

/** Query keys shared by the dashboard, the shell badges and the live updates. */
export const DASHBOARD_V2_KEY = ['dashboard-v2'] as const;
export const SUMMARY_V2_KEY = ['summary-v2'] as const;

/** Refetch interval of the dashboard and the badge summary. */
export const DASHBOARD_REFETCH_MS = 30_000;

/** Unified host state → design-system state. Disabled has no light: unknown. */
export function hostState(state: HostStateName | string | null | undefined): HealthState {
  switch (state) {
    case 'up': return 'ok';
    case 'degraded': return 'degraded';
    case 'warning': return 'warning';
    case 'down': return 'down';
    case 'maintenance': return 'maint';
    default: return 'unknown';
  }
}

export const HOST_STATE_LABEL: Record<HostStateName, string> = {
  up: 'Up',
  degraded: 'Degraded',
  warning: 'Warning',
  down: 'Down',
  unknown: 'No data',
  maintenance: 'Maintenance',
  disabled: 'Disabled',
};

export function hostStateLabel(state: HostStateName | string | null | undefined): string {
  return (state && HOST_STATE_LABEL[state as HostStateName]) || 'No data';
}

/** Order of the ring/legend and of every state bar (healthy → no data). */
const SEGMENT_ORDER: [HostStateName, HealthState][] = [
  ['up', 'ok'], ['degraded', 'degraded'], ['warning', 'warning'],
  ['down', 'down'], ['maintenance', 'maint'], ['unknown', 'unknown'],
];

/** Ring + legend segments from health.counts/reasons. Disabled hosts are not in the ring. */
export function healthSegments(health: Pick<HealthSection, 'counts' | 'reasons'>): HealthSegment[] {
  return SEGMENT_ORDER.map(([name, state]) => {
    const reasons = health.reasons?.[name] ?? [];
    const detail = reasons
      .slice(0, 2)
      .map((r) => (r.count > 1 ? `${r.text} ×${r.count}` : r.text))
      .join(' · ');
    return { state, count: health.counts?.[name] ?? 0, label: HOST_STATE_LABEL[name], detail: detail || undefined };
  });
}

/** Segments of a state distribution (group bars). Zero counts are dropped. */
export function stateSegments(byState: Partial<StateCounts>): HealthSegment[] {
  return SEGMENT_ORDER
    .map(([name, state]) => ({ state, count: byState[name] ?? 0, label: HOST_STATE_LABEL[name] }))
    .filter((s) => s.count > 0);
}

/** "27 up · 5 degraded · 2 down" in segment order. */
export function describeStates(byState: Partial<StateCounts>): string {
  const parts = stateSegments(byState).map((s) => `${s.count} ${s.label!.toLowerCase()}`);
  return parts.join(' · ') || 'No hosts';
}

/** Worst incident severity → the state its icon shows. */
export function severityTone(severity: string | null | undefined): 'crit' | 'warn' | 'info' {
  if (severity === 'critical') return 'crit';
  if (severity === 'warning') return 'warn';
  return 'info';
}

export function severityLabel(severity: string | null | undefined): string {
  if (severity === 'critical') return 'Critical';
  if (severity === 'warning') return 'Warning';
  if (severity === 'info') return 'Info';
  return severity || 'Unknown';
}

/** Affected-hosts text. `null` = not recorded ("unknown"), never "no hosts". */
export function incidentHostsLabel(hostCount: number | null | undefined): string {
  if (hostCount === null || hostCount === undefined) return 'affected hosts not recorded';
  if (hostCount === 0) return 'no hosts affected';
  return hostCount === 1 ? '1 host affected' : `${hostCount} hosts affected`;
}

/** Compact duration: "45 s", "25 min", "3 h 5 min", "2 d 4 h". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`;
}

export function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(t) ? null : t;
}

/** Seconds between `iso` and `now` (null when unknown). */
export function ageSeconds(iso: string | null | undefined, now: number): number | null {
  const t = parseTime(iso);
  return t === null ? null : Math.max(0, (now - t) / 1000);
}

/** "just now", "11 min ago", "3 h ago". */
export function formatAgo(iso: string | null | undefined, now: number): string {
  const a = ageSeconds(iso, now);
  if (a === null) return 'never';
  if (a < 45) return 'just now';
  return `${formatDuration(a)} ago`;
}

/** Older than `maxAgeSeconds` (or missing) = stale. */
export function isStale(iso: string | null | undefined, now: number, maxAgeSeconds: number): boolean {
  const a = ageSeconds(iso, now);
  return a === null || a > maxAgeSeconds;
}

/** "14:32" in the viewer's locale/zone. */
export function formatClock(iso: string | null | undefined): string {
  const t = parseTime(iso);
  if (t === null) return '—';
  return new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** "14:32" today, otherwise "8 Oct, 14:32". */
export function formatWhen(iso: string | null | undefined, now: number): string {
  const t = parseTime(iso);
  if (t === null) return '—';
  const d = new Date(t);
  if (d.toDateString() === new Date(now).toDateString()) return formatClock(iso);
  return d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(iso: string | null | undefined): string {
  const t = parseTime(iso);
  if (t === null) return '—';
  return new Date(t).toLocaleDateString([], { day: 'numeric', month: 'short' });
}

/** Number with at most `digits` decimals and no trailing noise ("—" for null). */
export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 100) return Math.round(v).toLocaleString();
  return Number(v.toFixed(digits)).toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Latency in ms with sensible precision: 0.32, 3.9, 14, 140. */
export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v < 1) return v.toFixed(2);
  if (v < 10) return v.toFixed(1);
  return Math.round(v).toString();
}

/** The error entry of a dashboard section, if it failed. */
export function sectionError(data: Pick<DashboardV2, 'errors'> | undefined, ...names: string[]): SectionError | null {
  return data?.errors?.find((e) => names.includes(e.section)) ?? null;
}

// ── Incidents card ───────────────────────────────────────────────────────

export interface DayBar {
  date: string;
  count: number;
  heightPct: number;
  today: boolean;
  label: string;
}

/** 14-day mini bars; the last entry is today. Empty days keep a 6 % stub. */
export function incidentDayBars(perDay: IncidentDay[]): DayBar[] {
  const max = Math.max(1, ...perDay.map((d) => d.count));
  return perDay.map((d, i) => {
    const today = i === perDay.length - 1;
    const date = new Date(`${d.date}T12:00:00`);
    const day = Number.isNaN(date.getTime())
      ? d.date
      : date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    return {
      date: d.date,
      count: d.count,
      heightPct: d.count ? (d.count / max) * 100 : 6,
      today,
      label: `${today ? 'Today' : day}: ${d.count} incident${d.count === 1 ? '' : 's'}`,
    };
  });
}

export function describeIncidentDays(perDay: IncidentDay[]): string {
  if (!perDay.length) return 'No incident history';
  const today = perDay[perDay.length - 1]?.count ?? 0;
  return `Incidents per day, last ${perDay.length} days: ${perDay.map((d) => d.count).join(', ')}. Today ${today}.`;
}

/** Incidents that touch any of these hosts. */
export function incidentsForHosts(items: DashboardIncident[], hostIds: number[]): DashboardIncident[] {
  const set = new Set(hostIds);
  return items.filter((i) => i.host_ids?.some((h) => set.has(h)));
}

// ── Internet card ────────────────────────────────────────────────────────

export interface SpeedBar {
  at: string | null;
  value: number | null;
  heightPct: number;
}

/** Download bars of the last tests, scaled to the max (no data = stub). */
export function speedBars(history: SpeedPoint[]): SpeedBar[] {
  const values = history.map((p) => p.download_mbps).filter((v): v is number => typeof v === 'number');
  const max = Math.max(1, ...values);
  return history.map((p) => ({
    at: p.at,
    value: p.download_mbps,
    heightPct: typeof p.download_mbps === 'number' ? Math.max(3, (p.download_mbps / max) * 100) : 3,
  }));
}

export function describeSpeedHistory(history: SpeedPoint[]): string {
  const values = history.map((p) => p.download_mbps).filter((v): v is number => typeof v === 'number');
  if (!values.length) return 'No speed test results';
  const latest = values[values.length - 1];
  return `Download speed of the last ${values.length} tests, between ${Math.round(Math.min(...values))} and ${Math.round(Math.max(...values))} Mbit/s, latest ${Math.round(latest)}`;
}

/** WAN pill state. Only UniFi knows the WAN; without it there is no pill. */
export function wanState(status: string | null | undefined): HealthState | null {
  if (!status) return null;
  if (status === 'up') return 'ok';
  if (status === 'down') return 'down';
  return 'unknown';
}

// ── Topology card ────────────────────────────────────────────────────────

export type Glyph = 'globe' | 'firewall' | 'switch' | 'ap' | 'server' | 'hyper' | 'nas' | 'printer' | 'probe' | 'vm' | 'cloud';

/**
 * Device glyph from what the API tells us. Hosts carry no device type, so
 * the name (and the link provenance) is the only hint; the fallback is a
 * neutral server glyph.
 */
export function glyphFor(name: string | null | undefined, hints: { gateway?: boolean; provenance?: string | null; probe?: boolean } = {}): Glyph {
  if (hints.probe) return 'probe';
  if (hints.gateway) return 'firewall';
  const n = (name ?? '').toLowerCase();
  const has = (re: RegExp) => re.test(n);
  if (has(/(^|[^a-z])(fw|firewall|opnsense|pfsense|fortigate|udm|usg|gw|gateway|router)([^a-z]|$)/)) return 'firewall';
  if (has(/(^|[^a-z])(sw|switch|usw)([^a-z]|$)|switch/)) return 'switch';
  if (has(/(^|[^a-z])(ap|uap|wap|wifi|wlan)([^a-z]|$)/)) return 'ap';
  if (has(/(^|[^a-z])(nas|synology|truenas|qnap|unas|storage)([^a-z]|$)/)) return 'nas';
  if (has(/(^|[^a-z])(pve|proxmox|esx|esxi|hyperv|hv|xcp)([^a-z0-9]|\d|$)/)) return 'hyper';
  if (has(/(^|[^a-z])(prn|printer|mfp)([^a-z]|$)/)) return 'printer';
  if (has(/probe/)) return 'probe';
  if (hints.provenance === 'proxmox' || has(/(^|[^a-z])(vm|ct|lxc)([^a-z]|$)/)) return 'vm';
  return 'server';
}

export interface TopoColumn {
  key: string;
  kind: 'parent' | 'probe';
  parent?: TopoParent;
  group?: HostGroup;
  /** Branch state for wire, border and glow. */
  tone: 'bad' | 'warn' | 'unknown' | 'ok';
}

function parentTone(p: TopoParent): TopoColumn['tone'] {
  if (p.worst_state === 'down') return 'bad';
  if (p.worst_state === 'warning') return 'warn';
  if (p.worst_state === 'unknown') return 'unknown';
  return 'ok';
}

/**
 * Up to `max` columns under the root row: affected parents first (the API
 * sorts worst first), then silent probes (their hosts have no data), then
 * the largest healthy parents. Parents already shown in the root row are
 * skipped unless nothing else is left.
 */
export function topologyColumns(topo: TopologySection, groups: HostGroup[] | null, rootIds: number[], max = 3): TopoColumn[] {
  const roots = new Set(rootIds);
  const candidates = topo.parents.filter((p) => !roots.has(p.id));
  const pool = candidates.length ? candidates : topo.parents;
  const affected = pool.filter((p) => p.affected);
  const healthy = pool.filter((p) => !p.affected).sort((a, b) => b.descendant_count - a.descendant_count);
  const silent = (groups ?? []).filter((g) => g.kind === 'probe' && g.fresh === false && g.host_count > 0);
  const cols: TopoColumn[] = [
    ...affected.map((p) => ({ key: `p${p.id}`, kind: 'parent' as const, parent: p, tone: parentTone(p) })),
    ...silent.map((g) => ({ key: `g${g.id ?? 'orphan'}`, kind: 'probe' as const, group: g, tone: 'unknown' as const })),
    ...healthy.map((p) => ({ key: `p${p.id}`, kind: 'parent' as const, parent: p, tone: parentTone(p) })),
  ];
  // Keep at least one silent probe visible when affected parents fill the row.
  const out = cols.slice(0, max);
  if (silent.length && !out.some((c) => c.kind === 'probe') && out.length === max) {
    out[max - 1] = cols.find((c) => c.kind === 'probe')!;
  }
  return out;
}

/** "Links from UniFi + Proxmox parent data" from links_by_provenance. */
export function describeProvenance(byProv: Record<string, number>): string {
  const names: Record<string, string> = { manual: 'manual', unifi: 'UniFi', proxmox: 'Proxmox' };
  const parts = Object.entries(byProv)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${names[k] ?? k} ${n}`);
  return parts.length ? `Links: ${parts.join(' · ')}` : 'No parent links yet';
}

// ── Groups card ──────────────────────────────────────────────────────────

/** Freshness line of a probe group. */
export function groupFreshness(g: HostGroup, now: number): { stale: boolean; text: string } {
  if (g.kind === 'direct') return { stale: false, text: describeStates(g.by_state) };
  if (g.id === null) return { stale: true, text: 'Assigned probe no longer exists · hosts have no data' };
  const window = g.staleness_window_seconds ? ` (threshold ${formatDuration(g.staleness_window_seconds)})` : '';
  if (g.fresh === false) {
    if (!g.last_report) return { stale: true, text: `Probe never reported${window}` };
    return {
      stale: true,
      text: `No data since ${formatWhen(g.last_report, now)} · probe silent ${formatDuration(ageSeconds(g.last_report, now))}${window}`,
    };
  }
  return { stale: false, text: `${describeStates(g.by_state)} · reported ${formatAgo(g.last_report, now)}` };
}

// ── Upcoming card ────────────────────────────────────────────────────────

export interface UpcomingView {
  icon: 'maintenance' | 'certificate' | 'disk';
  title: string;
  subtitle: string;
  when: string;
  whenDetail: string;
  /** Disk fill for the progress bar (0–100). */
  progressPct?: number;
  href: string | null;
}

function objectHref(o: { kind: string; id: number | null }): string | null {
  if (o.id === null || o.id === undefined) return o.kind === 'maintenance_window' ? '/alerts?tab=maintenance' : null;
  if (o.kind === 'host') return `/hosts/${o.id}`;
  if (o.kind === 'agent') return `/agents/${o.id}`;
  if (o.kind === 'maintenance_window') return '/alerts?tab=maintenance';
  return null;
}

export function upcomingView(item: UpcomingItem, now: number): UpcomingView {
  if (item.kind === 'maintenance') {
    const names = item.scope.all_hosts ? 'all hosts' : item.scope.host_names.join(', ') || `${item.scope.host_ids?.length ?? 0} hosts`;
    const range = item.starts_at ? `${formatClock(item.starts_at)}–${formatClock(item.ends_at)}` : `until ${formatWhen(item.ends_at, now)}`;
    const active = item.phase === 'active';
    const target = active ? item.ends_at : item.starts_at;
    const targetTs = parseTime(target);
    const delta = targetTs === null ? null : Math.max(0, (targetTs - now) / 1000);
    return {
      icon: 'maintenance',
      title: item.title,
      subtitle: `${names} · maintenance ${range}`,
      when: formatClock(target),
      whenDetail: `${active ? 'ends' : 'starts'} in ${formatDuration(delta)}`,
      href: '/alerts?tab=maintenance',
    };
  }
  if (item.kind === 'certificate') {
    return {
      icon: 'certificate',
      title: item.title,
      subtitle: [item.object.name, item.source].filter(Boolean).join(' · '),
      when: item.days <= 0 ? 'Expired' : `${item.days} day${item.days === 1 ? '' : 's'}`,
      whenDetail: formatDate(item.due_at),
      href: objectHref(item.object),
    };
  }
  const trend = item.trend_pct_per_day !== null && item.trend_pct_per_day !== undefined
    ? `+${fmtNum(item.trend_pct_per_day, 2)} %/day · ` : '';
  return {
    icon: 'disk',
    title: item.title,
    subtitle: `${trend}${item.method}`,
    when: `~${Math.max(0, Math.round(item.days))} day${Math.round(item.days) === 1 ? '' : 's'}`,
    whenDetail: 'until full',
    progressPct: item.current_pct ?? undefined,
    href: objectHref(item.object),
  };
}

// ── Latency card ─────────────────────────────────────────────────────────

/** Text alternative for the latency chart. */
export function describeLatency(l: LatencySection): string {
  const who = `${l.host_count} host${l.host_count === 1 ? '' : 's'} of incident #${l.incident_id}`;
  const parts = [`Latency of ${who}, last ${l.window_hours} h, per minute.`];
  if (!l.points.length) return `${parts[0]} No checks in this window.`;
  if (l.median_before_ms !== null) parts.push(`Median ${fmtMs(l.median_before_ms)} ms before the onset at ${formatClock(l.onset_at)}.`);
  if (l.median_since_ms !== null) parts.push(`Median ${fmtMs(l.median_since_ms)} ms since.`);
  const maxes = l.points.map((p) => p.max_ms).filter((v): v is number => v !== null);
  if (maxes.length) parts.push(`Highest ${fmtMs(Math.max(...maxes))} ms.`);
  const firstFail = l.points.find((p) => p.failed > 0);
  if (firstFail) parts.push(`Failed checks from ${formatClock(firstFail.t)}.`);
  return parts.join(' ');
}

// ── Syslog card ──────────────────────────────────────────────────────────

/** Where the current rate sits against the learned band (null = no band). */
export function syslogLevel(s: Pick<SyslogSection, 'current_per_min' | 'usual'>): 'above' | 'below' | 'within' | null {
  if (!s.usual) return null;
  if (s.current_per_min > s.usual.high_per_min) return 'above';
  if (s.current_per_min < s.usual.low_per_min) return 'below';
  return 'within';
}

/** Messages per minute of each bucket. */
export function syslogRates(s: Pick<SyslogSection, 'buckets' | 'bucket_minutes'>): { t: string; perMin: number; errors: number }[] {
  const m = s.bucket_minutes || 15;
  return s.buckets.map((b) => ({ t: b.t, perMin: Math.round((b.count / m) * 100) / 100, errors: b.errors }));
}

export function describeSyslog(s: SyslogSection): string {
  const band = s.usual ? ` Usual ${fmtNum(s.usual.low_per_min)} to ${fmtNum(s.usual.high_per_min)} per minute.` : ' No learned baseline yet.';
  return `Syslog rate, last 24 hours in ${s.bucket_minutes}-minute buckets. Now ${fmtNum(s.current_per_min)} messages per minute.${band} ${s.total_24h.toLocaleString()} messages, ${s.errors_24h.toLocaleString()} errors or worse.`;
}

// ── Availability card ────────────────────────────────────────────────────

/**
 * Scale for the availability bar: from `lo` to 100 %, wide enough to show
 * both the value and the target (99.9 % target → 99.8 … 100).
 */
export function availabilityScale(a: Pick<AvailabilitySection, 'pct' | 'target_pct'>): { lo: number; fillPct: number | null; targetPct: number } {
  const target = Math.min(100, Math.max(0, a.target_pct));
  let span = Math.max(2 * (100 - target), 0.1);
  if (a.pct !== null && a.pct !== undefined) span = Math.max(span, (100 - a.pct) * 1.25);
  span = Math.min(span, 100);
  const lo = Math.round((100 - span) * 1000) / 1000;
  const pos = (v: number) => Math.min(100, Math.max(0, ((v - lo) / (100 - lo)) * 100));
  return { lo, fillPct: a.pct === null || a.pct === undefined ? null : pos(a.pct), targetPct: pos(target) };
}

/** "99.94" — two decimals near 100, one below 99. */
export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v >= 99 ? v.toFixed(2) : v.toFixed(1);
}

// ── Changes ──────────────────────────────────────────────────────────────

export const CHANGE_LABEL: Record<ChangeType, string> = {
  incident_opened: 'Incidents opened',
  incident_resolved: 'Resolved',
  incident_acknowledged: 'Acknowledged',
  host_added: 'Hosts added',
  port_discovered: 'Ports discovered',
  agent_enrolled: 'Agents enrolled',
  agent_updated: 'Agents updated',
  maintenance_started: 'Maintenance started',
  probe_silent: 'Probes went silent',
  config_change: 'Config changes',
};

export const CHANGE_ORDER: ChangeType[] = [
  'incident_opened', 'incident_resolved', 'incident_acknowledged', 'probe_silent', 'host_added',
  'maintenance_started', 'agent_updated', 'agent_enrolled', 'port_discovered', 'config_change',
];

export interface ChangeTile {
  type: ChangeType;
  label: string;
  count: number;
  detail: string;
  items: ChangeItem[];
}

/**
 * Tiles for "Since your last visit": one per type with a count, in a fixed
 * order, with the newest items of that type as detail text. A host count
 * label says "discovered" when every listed host came from a scanner.
 */
export function changeTiles(counts: Partial<Record<ChangeType, number>>, items: ChangeItem[], max = 5): { tiles: ChangeTile[]; rest: number } {
  const all = CHANGE_ORDER
    .filter((t) => (counts[t] ?? 0) > 0)
    .map((type) => {
      const mine = items.filter((i) => i.type === type);
      let label = CHANGE_LABEL[type];
      if (type === 'host_added' && mine.length && mine.every((i) => i.detail?.discovered === true)) label = 'New hosts discovered';
      const detail = mine.slice(0, 2).map((i) => changeDetail(i)).join(' · ');
      return { type, label, count: counts[type] ?? 0, detail, items: mine };
    });
  const tiles = all.slice(0, max);
  const rest = all.slice(max).reduce((a, t) => a + t.count, 0);
  return { tiles, rest };
}

/** Short text for one change, without the time. */
export function changeDetail(i: ChangeItem): string {
  if (i.type === 'incident_resolved' && typeof i.detail?.duration_seconds === 'number') {
    return `${i.title} (${formatDuration(i.detail.duration_seconds)})`;
  }
  if (i.type === 'agent_updated' && i.detail?.to) return `${i.object.name ?? 'Agent'} → ${String(i.detail.to)}`;
  return i.title;
}

/** Where a change item links to, if anywhere. */
export function changeHref(i: ChangeItem): string | null {
  const id = i.object.id;
  switch (i.object.kind) {
    case 'incident': return id !== null ? `/incidents/${id}` : null;
    case 'host': return id !== null ? `/hosts/${id}` : null;
    case 'agent':
    case 'probe': return id !== null ? `/agents/${id}` : null;
    case 'maintenance_window': return '/alerts?tab=maintenance';
    default: return null;
  }
}

// ── Live updates ─────────────────────────────────────────────────────────

/**
 * True when any host's reachability flipped compared to the last ping seen
 * for it. Updates `last` in place. The first ping of a host never counts:
 * there is nothing to compare with yet (the dashboard polls anyway).
 */
export function reachabilityFlipped(last: Map<number, boolean>, pings: ReadonlyMap<number, { online: boolean }>): boolean {
  let flipped = false;
  pings.forEach((u, id) => {
    const prev = last.get(id);
    if (prev !== undefined && prev !== u.online) flipped = true;
    last.set(id, u.online);
  });
  return flipped;
}

/** First-run: nothing is monitored and nothing is connected yet. */
export function isEmptyInstall(d: DashboardV2 | undefined): boolean {
  const t = d?.health?.totals;
  if (!t) return false;
  return t.hosts === 0 && t.disabled === 0 && t.agents === 0 && t.integrations === 0;
}
