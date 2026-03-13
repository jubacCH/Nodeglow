import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { Incident } from '@/types';

export function useIncidents(status?: string) {
  return useQuery({
    queryKey: ['incidents', status],
    queryFn: () => get<Incident[]>(`/api/v1/incidents${status ? `?status=${status}` : ''}`),
    refetchInterval: 30_000,
  });
}
