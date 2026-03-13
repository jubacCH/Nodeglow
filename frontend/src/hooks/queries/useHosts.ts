import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { HostStatus, HostDetail, PingResult } from '@/types';

export function useHosts() {
  return useQuery({
    queryKey: ['hosts'],
    queryFn: () => get<HostStatus[]>('/hosts/api/status'),
    refetchInterval: 30_000,
  });
}

export function useHost(id: number) {
  return useQuery({
    queryKey: ['host', id],
    queryFn: () => get<HostDetail>(`/api/v1/hosts/${id}`),
    enabled: id > 0,
  });
}

export function useHostHistory(id: number, hours = 24) {
  return useQuery({
    queryKey: ['host-history', id, hours],
    queryFn: () =>
      get<{ host_id: number; count: number; results: PingResult[] }>(
        `/api/v1/hosts/${id}/history?hours=${hours}`,
      ),
    enabled: id > 0,
  });
}
