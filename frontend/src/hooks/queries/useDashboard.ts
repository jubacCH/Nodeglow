import { useCallback, useSyncExternalStore } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { whileLive } from '@/stores/websocket';
import type { HostStatus } from '@/types';
import type { HostListItem } from './useHosts';

export interface DashboardData {
  host_stats: HostStat[];
  online_count: number;
  offline_count: number;
  total_count: number;
  integration_health: IntHealth[];
  active_incidents: number;
  syslog_stats: SyslogStats;
  speedtest_data: SpeedtestData | null;
  storage_pools: StoragePool[];
  container_data: ContainerData | null;
  ups_data: UpsData | null;
  ssl_certs: SslCert[];
  recent_incidents: RecentIncident[];
  incident_trend: IncidentTrendDay[];
  heatmap_data: HeatmapHost[];
  heatmap_days: string[];
  layout: WidgetLayout[];
  top_latency: TopItem[];
  uptime_ranking: UptimeHost[];
  nodeglow_uptime: string;
  anomalies: Anomaly[];
  warnings: ResourceWarning[];
}

export interface HostStat {
  host: { id: number; name: string; hostname: string; source: string; check_type: string; maintenance: boolean; port_error: boolean };
  online: boolean | null;
  latency: number | null;
  sparkline: number[];
  uptime_pct: number;
  avg_latency: number | null;
  health_score: number;
  uptime_stats?: { h24: number; d7: number; d30: number };
}

export interface IntHealth {
  type: string;
  label: string;
  color: string;
  name: string;
  config_id: number;
  ok: boolean;
  error: string | null;
  last_check: string | null;
  single_instance: boolean;
  health_score?: number;
}

export interface SyslogStats {
  total_24h: number;
  errors_24h: number;
  rate_data: { labels: string[]; errors: number[]; warnings: number[]; info: number[] };
}

export interface SpeedtestData {
  download_mbps: number;
  upload_mbps: number;
  ping_ms: number | null;
  server_name: string;
  timestamp: string;
}

export interface StoragePool {
  name: string;
  source: string;
  healthy: boolean;
  pct: number;
  used_gb: number;
  total_gb: number;
  days_until_full?: number;
  trend_pct_per_day?: number;
}

export interface ContainerData {
  running: number;
  stopped: number;
  updates_available?: number;
  containers: ContainerInfo[];
  // Legacy Portainer format (backwards compat)
  environments?: ContainerEnv[];
}

export interface ContainerEnv {
  name: string;
  containers_running: number;
  containers_stopped: number;
  containers: ContainerInfo[];
}

export interface ContainerInfo {
  name: string;
  image: string;
  state?: string;
  status?: string;
  host?: string;
  source?: string;
  cpu_pct?: number;
  mem_pct?: number;
  mem_mb?: number;
  health?: string;
  restart_count?: number;
  update_available?: boolean;
}

export interface UpsData {
  units: UpsUnit[];
  on_battery: boolean;
}

export interface UpsUnit {
  name: string;
  status_label: string;
  on_battery: boolean;
  battery_pct: number;
  load_pct: number;
  runtime_s: number;
  model: string;
}

export interface SslCert {
  host_id: number | null;
  name: string;
  days: number | null;
  source?: string;
}

export interface RecentIncident {
  id: number;
  title: string;
  severity: string;
  status: string;
  summary: string | null;
  created_at: string;
}

export interface IncidentTrendDay {
  date: string;
  critical: number;
  warning: number;
  info: number;
}

export interface HeatmapHost {
  host_id: number;
  name: string;
  days: (number | null)[];
}

export interface WidgetLayout {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TopItem {
  id: number;
  name: string;
  value: number;
}

export interface UptimeHost {
  host_id: number;
  name: string;
  uptime: number;
}

export interface Anomaly {
  name: string;
  type: string;
  node: string;
  cluster_name: string;
  metric: string;
  current: number;
  mean: number;
  factor: number | null;
  sustained: number | null;
  severity: number;
  host_id?: number;
}

export interface ResourceWarning {
  name: string;
  type: string;
  node: string;
  cluster_name: string;
  metric: string;
  current: number;
  threshold: number;
}

/**
 * The full dashboard payload (every host with sparklines, heatmaps, …).
 * Heavy — use it on the dashboard page only. App-wide chrome (sidebar,
 * command palette) has lighter sources below.
 */
export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () =>
      get<DashboardData>('/api/dashboard'),
    // Host online/latency is patched in live from the WebSocket.
    refetchInterval: whileLive(30_000, 60_000),
  });
}

export function useNavCounts() {
  return useQuery({
    queryKey: ['nav-counts'],
    queryFn: () => get<Record<string, number>>('/api/v2/nav-counts'),
    refetchInterval: 60_000,
  });
}

export interface SystemSummary {
  hosts: { total: number; enabled: number };
  agents: { total: number };
  integrations: { total: number };
  incidents: { open: number };
}

/** Cheap COUNT(*) summary (/api/v1/status) — used for the open-incident badge. */
export function useSystemSummary() {
  return useQuery({
    queryKey: ['v1-status'],
    queryFn: () => get<SystemSummary>('/api/v1/status'),
    refetchInterval: 60_000,
  });
}

export interface HostSearchResult {
  id: number;
  name: string;
  hostname: string;
  enabled: boolean;
  online: boolean | null;
}

/**
 * Server-side host search (/hosts/api/search, name/hostname, max 10).
 * Only runs for queries of 2+ characters and while `enabled`.
 */
export function useHostSearch(q: string, enabled = true) {
  const query = q.trim();
  return useQuery({
    queryKey: ['host-search', query.toLowerCase()],
    queryFn: () => get<HostSearchResult[]>(`/hosts/api/search?q=${encodeURIComponent(query)}`),
    enabled: enabled && query.length >= 2,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}

function offlineCountFromCache(qc: QueryClient): number | null {
  const dash = qc.getQueryData<DashboardData>(['dashboard']);
  if (dash) return dash.offline_count ?? 0;
  const hosts = qc.getQueryData<HostStatus[]>(['hosts']);
  if (hosts) return hosts.filter((h) => !h.maintenance && h.online === false).length;
  const v1 = qc.getQueryData<HostListItem[]>(['hosts-v1']);
  if (v1) return v1.filter((h) => h.status === 'offline').length;
  return null;
}

/**
 * Offline-host count derived from whatever host data is already cached
 * (dashboard, hosts list), kept live by the WebSocket patches. It never
 * fetches on its own — there is no lightweight count endpoint yet — so it
 * returns null until one of those pages has loaded its data.
 */
export function useCachedOfflineCount(): number | null {
  const qc = useQueryClient();
  return useSyncExternalStore(
    useCallback((cb) => qc.getQueryCache().subscribe(cb), [qc]),
    () => offlineCountFromCache(qc),
    () => null,
  );
}
