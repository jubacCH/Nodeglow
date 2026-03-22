import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

export interface SeverityItem {
  severity: number;
  label: string;
  count: number;
}

export interface HostItem {
  hostname: string;
  source_ip: string;
  count: number;
}

export interface AppItem {
  app_name: string;
  count: number;
}

export interface RateBucket {
  bucket: string;
  count: number;
  errors: number;
}

export interface GeoItem {
  country: string;
  count: number;
}

export interface SyslogStatsResponse {
  total: number;
  severity_distribution: SeverityItem[];
  top_hosts: HostItem[];
  top_apps: AppItem[];
  message_rate: RateBucket[];
  geo_distribution: GeoItem[];
}

export function useSyslogStats(hours: number) {
  return useQuery({
    queryKey: ['syslog-stats', hours],
    queryFn: () => get<SyslogStatsResponse>(`/api/v1/syslog/stats?hours=${hours}`),
    refetchInterval: 30_000,
  });
}
