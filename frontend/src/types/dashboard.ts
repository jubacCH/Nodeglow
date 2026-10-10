/**
 * Response shapes of the v2 read models for the E3 dashboard
 * (backend/routers/api_v2.py, services/dashboard_v2.py, services/changes.py).
 * Field docs: docs/design/05-dashboard-api.md. All times are UTC ISO-8601
 * with a trailing `Z`; `null` means "not derivable", never zero.
 */

/** Unified host state (services/host_state.py), worst first. */
export type HostStateName = 'down' | 'warning' | 'degraded' | 'unknown' | 'maintenance' | 'up' | 'disabled';

export type Severity = 'critical' | 'warning' | 'info';

export type StateCounts = Record<HostStateName, number>;

export interface StateReason {
  text: string;
  count: number;
}

export interface IncidentCounts {
  open: number;
  acknowledged: number;
  unacknowledged: number;
  critical: number;
  warning: number;
  info: number;
}

/** GET /api/v2/summary (also embedded as `summary` in the dashboard). */
export interface DashboardSummary {
  generated_at?: string;
  incidents: IncidentCounts;
  hosts: { total: number; by_state: StateCounts; attention: number };
  probes: { total: number; stale: number; stale_unused: number };
  integrations: { total: number; ok: number; error: number; no_data: number };
  definitions?: Record<string, string>;
}

export interface FailingIntegration {
  id: number;
  type: string;
  name: string;
  error: string | null;
  since_check: string | null;
}

export interface HealthSection {
  counts: StateCounts;
  reasons: Partial<Record<HostStateName, StateReason[]>>;
  worst: HostStateName | null;
  totals: {
    hosts: number;
    disabled: number;
    with_current_data: number;
    agents: number;
    agents_reporting: number;
    integrations: number;
    integrations_error: number;
    probes: number;
    probes_stale: number;
  };
  failing_integrations?: FailingIntegration[];
}

export interface SpeedPoint {
  at: string | null;
  download_mbps: number | null;
  upload_mbps: number | null;
  latency_ms: number | null;
}

export interface InternetSection {
  source: 'speedtest' | 'unifi' | null;
  latest: (SpeedPoint & { server: string | null }) | null;
  /** Last 24 results, oldest first. */
  history: SpeedPoint[];
  /** Only with UniFi; `status` is "up"/"down" or the raw lower-cased value. */
  wan: { status: string; raw_status?: string | null; latency_ms: number | null; source: string; integration?: string } | null;
  gateway: { id: number; name: string; state: HostStateName | null } | null;
}

export interface DashboardIncident {
  id: number;
  title: string;
  severity: Severity | string;
  rule: string;
  status: 'open' | 'acknowledged' | string;
  acknowledged: boolean;
  acknowledged_by: string | null;
  opened_at: string | null;
  age_seconds: number;
  /** null = affected hosts were not recorded for this incident. */
  host_count: number | null;
  host_ids: number[] | null;
  summary: string | null;
}

export interface IncidentDay {
  date: string;
  count: number;
  critical: number;
  warning: number;
  info: number;
}

export interface ResolvedIncident {
  id: number;
  title: string;
  severity: Severity | string;
  opened_at: string | null;
  resolved_at: string | null;
  duration_seconds: number;
}

export interface IncidentsSection {
  counts: IncidentCounts;
  items: DashboardIncident[];
  per_day: IncidentDay[];
  resolved_today: ResolvedIncident[];
  day_starts_at: string | null;
}

export interface TopoNode {
  id: number;
  name: string;
  state: HostStateName | null;
  state_reason: string | null;
}

export interface TopoChild extends TopoNode {
  provenance: string | null;
  child_count: number;
}

export interface TopoParent extends TopoNode {
  parent_id: number | null;
  child_count: number;
  descendant_count: number;
  descendant_states: Partial<StateCounts>;
  worst_state: HostStateName | null;
  affected: boolean;
  link_provenance: Record<string, number>;
  is_gateway: boolean;
  /** Only for affected parents, worst first. */
  children?: TopoChild[];
}

export interface TopoRoot {
  id: number;
  name: string;
  state: HostStateName | null;
  is_gateway: boolean;
  descendant_count: number;
  worst_state: HostStateName | null;
}

export interface TopologySection {
  roots: TopoRoot[];
  parents: TopoParent[];
  parents_total: number;
  unlinked_hosts: number;
  links_by_provenance: Record<string, number>;
  internet_root: {
    state: string | null;
    latency_ms: number | null;
    gateway: InternetSection['gateway'];
    source: string | null;
  } | null;
}

/** "Direct" (checked by the core) or one group per probe — never a site. */
export interface HostGroup {
  kind: 'direct' | 'probe';
  id: number | null;
  name: string;
  description: string;
  host_count: number;
  by_state: Partial<StateCounts>;
  /** null for "Direct"; false = the probe is silent (its hosts are unknown). */
  fresh: boolean | null;
  last_report?: string | null;
  staleness_window_seconds?: number;
}

interface UpcomingObject {
  kind: string;
  id: number | null;
  name: string | null;
}

export interface MaintenanceUpcoming {
  kind: 'maintenance';
  phase: 'active' | 'scheduled';
  due_at: string | null;
  starts_at: string | null;
  ends_at: string | null;
  title: string;
  object: UpcomingObject;
  scope: { all_hosts: boolean; host_ids: number[] | null; host_names: string[] };
}

export interface CertificateUpcoming {
  kind: 'certificate';
  due_at: string | null;
  days: number;
  title: string;
  estimated_due: true;
  object: UpcomingObject;
  source: string | null;
}

export interface DiskUpcoming {
  kind: 'disk';
  due_at: string | null;
  days: number;
  title: string;
  estimated_due: true;
  method: string;
  object: UpcomingObject;
  current_pct: number | null;
  trend_pct_per_day: number | null;
  confidence: number | null;
  source: string | null;
}

export type UpcomingItem = MaintenanceUpcoming | CertificateUpcoming | DiskUpcoming;

export interface UpcomingSection {
  items: UpcomingItem[];
  agent_disk_predictions_ready: boolean;
}

export interface LatencyPoint {
  t: string;
  median_ms: number | null;
  max_ms: number | null;
  ok: number;
  failed: number;
}

export interface LatencySection {
  incident_id: number;
  title: string;
  severity: Severity | string;
  host_ids: number[];
  host_names: (string | null)[];
  host_count: number;
  onset_at: string | null;
  window_hours: number;
  median_before_ms: number | null;
  median_since_ms: number | null;
  points: LatencyPoint[];
}

export interface SyslogSection {
  bucket_minutes: number;
  buckets: { t: string; count: number; errors: number }[];
  current_per_min: number;
  /** Learned band for this hour/weekday; null until baselines exist. */
  usual: { low_per_min: number; high_per_min: number; mean_per_min: number; sources: number; method: string } | null;
  total_24h: number;
  errors_24h: number;
  receiving: boolean;
}

export interface AvailabilitySection {
  window_days: number;
  target_pct: number;
  budget_minutes: number;
  pct: number | null;
  downtime_minutes: number | null;
  checks: number;
  failed_checks: number;
  hosts_with_data: number;
  hosts_total: number;
  status: 'above_target' | 'below_target' | 'no_data';
  method: string;
  budget_used_pct?: number | null;
}

export type ChangeType =
  | 'incident_opened' | 'incident_resolved' | 'incident_acknowledged'
  | 'host_added' | 'port_discovered' | 'agent_enrolled' | 'agent_updated'
  | 'maintenance_started' | 'probe_silent' | 'config_change';

export interface ChangeItem {
  type: ChangeType;
  at: string | null;
  title: string;
  object: { kind: string; id: number | null; name: string | null };
  detail: Record<string, unknown> | null;
}

export interface SinceLastVisitSection {
  since: string | null;
  /** true = no stored visit, the window is the last 24 h. */
  fallback: boolean;
  counts: Partial<Record<ChangeType, number>>;
  total: number;
  items: ChangeItem[];
}

/** GET /api/v2/changes */
export interface ChangesResponse {
  since: string | null;
  until: string | null;
  total: number;
  counts: Partial<Record<ChangeType, number>>;
  limit: number;
  offset: number;
  has_more: boolean;
  items: ChangeItem[];
}

export type DashboardSectionName =
  | 'summary' | 'health' | 'internet' | 'incidents' | 'topology' | 'groups'
  | 'upcoming' | 'latency' | 'syslog' | 'availability' | 'since_last_visit'
  | '_integrations' | '_agents';

export interface SectionError {
  section: DashboardSectionName | string;
  error: string;
}

/** GET /api/v2/dashboard — every section is null when it failed (see errors). */
export interface DashboardV2 {
  generated_at: string | null;
  previous_seen_at: string | null;
  timezone: string;
  summary: DashboardSummary | null;
  health: HealthSection | null;
  internet: InternetSection | null;
  incidents: IncidentsSection | null;
  topology: TopologySection | null;
  groups: HostGroup[] | null;
  upcoming: UpcomingSection | null;
  latency: LatencySection | null;
  syslog: SyslogSection | null;
  availability: AvailabilitySection | null;
  since_last_visit: SinceLastVisitSection | null;
  errors: SectionError[];
  timings_ms?: Record<string, number>;
}

/** POST/GET /api/v2/me/seen */
export interface SeenResponse {
  dashboard_seen_at: string | null;
  previous_seen_at?: string | null;
}

/** Subset of GET /api/v1/incidents/{id} the side panel needs. */
export interface IncidentPreview {
  id: number;
  title: string;
  severity: Severity | string;
  status: string;
  rule: string;
  created_at: string;
  acknowledged_by: string | null;
  host_count: number | null;
  hosts: { id: number; name: string | null; hostname: string | null; state: HostStateName | null; state_reason: string | null }[] | null;
}

/** Subset of GET /api/v1/hosts/{id} the side panel needs. */
export interface HostPreview {
  id: number;
  name: string;
  hostname: string;
  check_type: string;
  enabled: boolean;
  state: HostStateName;
  state_reason: string | null;
  observed_at: string | null;
  probe_id: number | null;
  maintenance_until: string | null;
  source: string;
  latest?: { online: boolean | null; latency_ms: number | null; timestamp?: string | null } | null;
}
