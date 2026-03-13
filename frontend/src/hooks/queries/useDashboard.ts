import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

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
  container_data: ContainerInfo[];
  ups_data: UpsData | null;
  ssl_certs: SslCert[];
  recent_incidents: RecentIncident[];
  heatmap_data: HeatmapHost[];
  heatmap_days: string[];
  layout: WidgetLayout[];
  top_latency: TopItem[];
  uptime_ranking: UptimeHost[];
  nodeglow_uptime: string;
}

export interface HostStat {
  host: { id: number; name: string; hostname: string; source: string; check_type: string; maintenance: boolean };
  online: boolean | null;
  latency: number | null;
  sparkline: number[];
  uptime_stats: { h24: number; d7: number; d30: number };
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
}

export interface SyslogStats {
  total_24h: number;
  errors_24h: number;
  rate_data: { labels: string[]; errors: number[]; warnings: number[]; info: number[] };
}

export interface SpeedtestData {
  download_mbps: number;
  upload_mbps: number;
  ping_ms: number;
  isp: string;
  server: string;
  timestamp: string;
}

export interface StoragePool {
  integration: string;
  label: string;
  color: string;
  name: string;
  pool_name: string;
  used_pct: number;
  used_human: string;
  total_human: string;
  health: string;
}

export interface ContainerInfo {
  integration: string;
  name: string;
  status: string;
  image: string;
}

export interface UpsData {
  status: string;
  load_pct: number;
  battery_pct: number;
  voltage: number;
  temperature: number | null;
}

export interface SslCert {
  host_id: number;
  name: string;
  hostname: string;
  days_left: number;
  issuer: string;
}

export interface RecentIncident {
  id: number;
  title: string;
  severity: string;
  status: string;
  created_at: string;
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
  id: number;
  name: string;
  uptime_pct: number;
}

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () =>
      get<DashboardData>('/', ),
    refetchInterval: 30_000,
  });
}

export function useNavCounts() {
  return useQuery({
    queryKey: ['nav-counts'],
    queryFn: () => get<Record<string, number>>('/api/v2/nav-counts'),
    refetchInterval: 60_000,
  });
}
