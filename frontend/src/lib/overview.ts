/**
 * Read-model helpers for the concept F overview (docs/design/directions/concept-f.md).
 * Pure functions over the /api/v2/dashboard payload, so they are unit-tested
 * without rendering. Rule of the page: every problem appears in exactly one
 * place, and lists are capped so the page stays the same size at 50 or 500 hosts.
 */
import type {
  DashboardIncident, DashboardV2, HealthSection, HostGroup, HostStateName, StateCounts,
  SyslogSection, TopoChild, TopologySection,
} from '@/types/dashboard';
import { formatAgo, formatDuration, ageSeconds, upcomingView } from './dashboard';

// ── Head sentence ────────────────────────────────────────────────────────

export interface HeroCounts {
  /** Hosts that count (disabled ones are left out, they have no light). */
  total: number;
  healthy: number;
  /** down + warning + degraded */
  issues: number;
  noData: number;
  maintenance: number;
}

export function heroCounts(health: Pick<HealthSection, 'counts'>): HeroCounts {
  const c = health.counts;
  const n = (k: HostStateName) => c?.[k] ?? 0;
  const issues = n('down') + n('warning') + n('degraded');
  return {
    total: n('up') + issues + n('unknown') + n('maintenance'),
    healthy: n('up'),
    issues,
    noData: n('unknown'),
    maintenance: n('maintenance'),
  };
}

// ── Incident → cause path ────────────────────────────────────────────────

export interface PathNode {
  id: number;
  name: string;
  state: HostStateName | null;
}

export interface IncidentCause {
  /** From the topology root down to (and including) the parent the hosts share. */
  chain: PathNode[];
  /** How many of the incident's hosts sit directly below the last node of the chain. */
  shared: number;
  /** States of those hosts, from the topology children of the cause. */
  byState: Partial<StateCounts>;
}

/**
 * The parent most of an incident's hosts share in the topology, with its
 * ancestors. Only from structural links (topology parent ids), so the page may
 * label it "Confirmed". Returns null when the incident has no recorded hosts,
 * or when no parent holds at least two of them or half of them.
 */
export function incidentCause(
  inc: Pick<DashboardIncident, 'host_ids'>,
  topo: Pick<TopologySection, 'parents' | 'roots'> | null | undefined,
): IncidentCause | null {
  const ids = inc.host_ids;
  if (!ids?.length || !topo) return null;
  const want = new Set(ids);
  let best: { parent: TopologySection['parents'][number]; kids: TopoChild[] } | null = null;
  for (const p of topo.parents) {
    const kids = (p.children ?? []).filter((k) => want.has(k.id));
    if (kids.length > (best?.kids.length ?? 0)) best = { parent: p, kids };
  }
  if (!best || (best.kids.length < 2 && best.kids.length < ids.length / 2)) return null;

  const byId = new Map<number, PathNode & { parent_id: number | null }>();
  for (const p of topo.parents) byId.set(p.id, { id: p.id, name: p.name, state: p.state, parent_id: p.parent_id });
  for (const r of topo.roots) if (!byId.has(r.id)) byId.set(r.id, { id: r.id, name: r.name, state: r.state, parent_id: null });

  const chain: PathNode[] = [];
  const seen = new Set<number>();
  let cur: (PathNode & { parent_id: number | null }) | undefined = byId.get(best.parent.id);
  while (cur && !seen.has(cur.id) && chain.length < 6) {
    seen.add(cur.id);
    chain.unshift({ id: cur.id, name: cur.name, state: cur.state });
    cur = cur.parent_id === null ? undefined : byId.get(cur.parent_id);
  }
  const byState: Partial<StateCounts> = {};
  for (const k of best.kids) {
    const s = (k.state ?? 'unknown') as HostStateName;
    byState[s] = (byState[s] ?? 0) + 1;
  }
  return { chain, shared: best.kids.length, byState };
}

// ── Needs a look ─────────────────────────────────────────────────────────

export type NeedTone = 'down' | 'warning' | 'unknown' | 'info';
export type NeedIcon = 'probe' | 'host' | 'integration' | 'disk' | 'certificate' | 'discovery';

export interface NeedItem {
  key: string;
  tone: NeedTone;
  icon: NeedIcon;
  title: string;
  detail: string;
  href: string;
  action: string;
}

/** Host ids of all open incidents; null when at least one incident did not record its hosts. */
function incidentHostIds(items: DashboardIncident[]): Set<number> | null {
  const out = new Set<number>();
  for (const i of items) {
    if (i.host_ids === null) return null;
    for (const id of i.host_ids) out.add(id);
  }
  return out;
}

/**
 * Everything that wants attention and is not an open incident: silent
 * probes (folded per probe), failing integrations, hosts with issues outside
 * every incident, capacity and certificate forecasts (aggregated when there
 * are several), new hosts from discovery. Worst first.
 */
export function needItems(
  d: Pick<DashboardV2, 'groups' | 'health' | 'incidents' | 'upcoming'> | undefined,
  navCounts: Record<string, number> | undefined,
  now: number,
): NeedItem[] {
  if (!d) return [];
  const out: NeedItem[] = [];

  for (const g of d.groups ?? []) {
    if (g.kind !== 'probe' || g.fresh !== false || g.host_count === 0) continue;
    const age = ageSeconds(g.last_report, now);
    out.push({
      key: `probe-${g.id}`,
      tone: 'unknown',
      icon: 'probe',
      title: `${g.name} silent${age !== null ? ` for ${formatDuration(age)}` : ''}`,
      detail: `${g.host_count} host${g.host_count === 1 ? '' : 's'} without data · unknown, not up`,
      href: '/agents',
      action: 'Check',
    });
  }

  const issues = (d.health?.counts?.down ?? 0) + (d.health?.counts?.warning ?? 0) + (d.health?.counts?.degraded ?? 0);
  const inIncidents = incidentHostIds(d.incidents?.items ?? []);
  if (issues > 0 && inIncidents !== null) {
    const outside = Math.max(0, issues - inIncidents.size);
    if (outside > 0) {
      const reasons = [...(d.health?.reasons?.down ?? []), ...(d.health?.reasons?.warning ?? []), ...(d.health?.reasons?.degraded ?? [])]
        .slice(0, 2).map((r) => r.text).join(' · ');
      out.push({
        key: 'hosts-outside',
        tone: (d.health?.counts?.down ?? 0) > 0 ? 'down' : 'warning',
        icon: 'host',
        title: `${outside} host${outside === 1 ? '' : 's'} with issues outside every incident`,
        detail: reasons || 'No alert rule covers them',
        // The hosts page filters one state at a time: open it on the worst one.
        href: `/hosts?state=${(d.health?.counts?.down ?? 0) > 0 ? 'down' : (d.health?.counts?.warning ?? 0) > 0 ? 'warning' : 'degraded'}`,
        action: 'Show',
      });
    }
  }

  for (const f of d.health?.failing_integrations ?? []) {
    out.push({
      key: `integration-${f.id}`,
      tone: 'warning',
      icon: 'integration',
      title: `${f.name} is failing`,
      detail: f.error ?? `${f.type} integration`,
      href: `/integration/${f.type}`,
      action: 'Open',
    });
  }

  const items = d.upcoming?.items ?? [];
  const disks = items.filter((i) => i.kind === 'disk');
  if (disks.length === 1) {
    const v = upcomingView(disks[0], now);
    out.push({ key: 'disk', tone: 'warning', icon: 'disk', title: `${v.title} full in ${v.when}`, detail: v.subtitle, href: v.href ?? '/hosts', action: 'Open' });
  } else if (disks.length > 1) {
    const first = [...disks].sort((a, b) => a.days - b.days)[0];
    out.push({
      key: 'disk', tone: 'warning', icon: 'disk',
      title: `${disks.length} volumes full within ${Math.max(...disks.map((x) => Math.ceil(x.days)))} days`,
      detail: `Earliest: ${first.title} in ~${Math.max(0, Math.round(first.days))} days`,
      href: '/hosts', action: 'Review',
    });
  }
  const certs = items.filter((i) => i.kind === 'certificate');
  if (certs.length === 1) {
    const v = upcomingView(certs[0], now);
    out.push({ key: 'cert', tone: 'warning', icon: 'certificate', title: `Certificate expires in ${v.when}`, detail: v.subtitle || v.title, href: v.href ?? '/ssl', action: 'Details' });
  } else if (certs.length > 1) {
    const first = [...certs].sort((a, b) => a.days - b.days)[0];
    out.push({
      key: 'cert', tone: 'warning', icon: 'certificate',
      title: `${certs.length} certificates expire within 30 days`,
      detail: `Earliest: ${first.object.name ?? first.title} in ${first.days} day${first.days === 1 ? '' : 's'}`,
      href: '/ssl', action: 'Details',
    });
  }

  const found = navCounts?.discovery ?? 0;
  if (found > 0) {
    out.push({ key: 'discovery', tone: 'info', icon: 'discovery', title: `${found} new host${found === 1 ? '' : 's'} found`, detail: 'Discovery · awaiting review', href: '/tasks', action: 'Review' });
  }

  const rank: Record<NeedTone, number> = { down: 0, unknown: 1, warning: 2, info: 3 };
  return out.map((x, i) => ({ x, i })).sort((a, b) => rank[a.x.tone] - rank[b.x.tone] || a.i - b.i).map(({ x }) => x);
}

// ── Hosts by group ───────────────────────────────────────────────────────

/** Groups worst first: silent probes, then down, warning and degraded counts. Empty unused probes are left out. */
export function rankGroups(groups: HostGroup[]): HostGroup[] {
  const score = (g: HostGroup) => (g.fresh === false && g.host_count > 0 ? 1_000_000 : 0)
    + (g.by_state.down ?? 0) * 10_000 + (g.by_state.warning ?? 0) * 100 + (g.by_state.degraded ?? 0);
  return groups
    .filter((g) => g.kind === 'direct' || g.host_count > 0 || g.fresh === false)
    .map((g, i) => ({ g, i }))
    .sort((a, b) => score(b.g) - score(a.g) || a.i - b.i)
    .map(({ g }) => g);
}

/** "26 healthy · 10 issues · 1 maint." for a group row. */
export function groupSummary(g: Pick<HostGroup, 'by_state' | 'host_count' | 'fresh'>): string {
  const s = g.by_state;
  if (g.fresh === false && g.host_count > 0) return 'no data · probe silent';
  const issues = (s.down ?? 0) + (s.warning ?? 0) + (s.degraded ?? 0);
  const parts = [`${s.up ?? 0} healthy`];
  if (issues) parts.push(`${issues} issue${issues === 1 ? '' : 's'}`);
  if (s.unknown) parts.push(`${s.unknown} no data`);
  if (s.maintenance) parts.push(`${s.maintenance} maint.`);
  return parts.join(' · ');
}

// ── Sparklines ───────────────────────────────────────────────────────────

/** Syslog messages per minute, averaged per hour (oldest first). */
export function hourlySyslog(s: Pick<SyslogSection, 'buckets' | 'bucket_minutes'>): number[] {
  const m = s.bucket_minutes || 15;
  const per = Math.max(1, Math.round(60 / m));
  const out: number[] = [];
  for (let i = 0; i < s.buckets.length; i += per) {
    const chunk = s.buckets.slice(i, i + per);
    const count = chunk.reduce((a, b) => a + b.count, 0);
    out.push(Math.round((count / (chunk.length * m)) * 10) / 10);
  }
  return out;
}

/** "since 14:07 · 25 min" style age line for an incident. */
export function incidentAge(inc: Pick<DashboardIncident, 'age_seconds' | 'opened_at'>, now: number): string {
  return inc.opened_at ? formatAgo(inc.opened_at, now) : formatDuration(inc.age_seconds);
}
