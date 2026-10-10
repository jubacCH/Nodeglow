import type { HttpOptions } from '@/types';

export interface DiskInfo {
  mount: string;
  total_gb: number;
  used_gb: number;
  pct: number;
}

export interface DockerContainer {
  name: string;
  image: string;
  state?: string;
  status?: string;
  cpu_pct?: number;
  mem_pct?: number;
  mem_mb?: number;
  health?: string;
  restart_count?: number;
  update_available?: boolean;
}

export interface AgentMetrics {
  agent_id: number;
  agent_name: string;
  platform: string | null;
  arch: string | null;
  agent_version: string | null;
  last_seen: string | null;
  cpu_pct: number | null;
  mem_pct: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  disk_pct: number | null;
  load_1: number | null;
  load_5: number | null;
  load_15: number | null;
  uptime_s: number | null;
  rx_bytes: number | null;
  tx_bytes: number | null;
  snapshot_time: string | null;
  extra: {
    disks?: DiskInfo[];
    docker_containers?: DockerContainer[];
    [key: string]: unknown;
  } | null;
}

export interface PortInfo {
  idx: number;
  name: string;
  enable: boolean;
  up: boolean;
  speed: number;
  speed_label: string;
  is_uplink: boolean;
  poe_enable: boolean;
  poe_power: number;
  rx_bytes_r: number;
  tx_bytes_r: number;
  rx_bytes: number;
  tx_bytes: number;
  satisfaction: number;
  op_mode: string;
}

export interface ConnectedClient {
  mac: string;
  hostname: string;
  ip: string;
  sw_port?: number;
  is_wireless: boolean;
  rx_bytes_r: number;
  tx_bytes_r: number;
  vlan: number;
  ssid: string;
  signal: number;
}

/** Integration device data (UniFi, Proxmox …); shape varies by integration. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type DeviceData = Record<string, any>;

/** GET /api/v1/hosts/{id}. */
export interface HostDetailData {
  id: number;
  name: string;
  hostname: string;
  check_type: string;
  port: number | null;
  enabled: boolean;
  status: string;
  state?: string;
  state_reason?: string | null;
  observed_at?: string | null;
  probe_id: number | null;
  maintenance: boolean;
  maintenance_manual?: boolean;
  maintenance_window?: { id: number; name: string; ends_at: string | null } | null;
  maintenance_until: string | null;
  source: string;
  source_detail: string | null;
  latency_threshold_ms: number | null;
  ssl_expiry_days: number | null;
  mac_address: string | null;
  parent_id: number | null;
  port_error: boolean;
  check_detail: Record<string, boolean> | null;
  check_errors: Record<string, string> | null;
  http_options?: Partial<HttpOptions> | null;
  created_at: string | null;
  latest: { online: boolean | null; latency_ms: number | null; timestamp: string | null } | null;
  uptime: { h24: number | null; d7: number | null; d30: number | null };
  health_score?: number;
  health_pct?: number;
  agent: AgentMetrics | null;
  integration: {
    type: string;
    config_id: number;
    config_name: string;
    ok: boolean;
    timestamp: string;
    data: unknown;
    device: DeviceData | null;
  } | null;
}
