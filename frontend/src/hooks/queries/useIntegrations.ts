import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { IntegrationConfig, IntegrationSnapshot } from '@/types';

export function useIntegrations(type?: string) {
  return useQuery({
    queryKey: ['integrations', type],
    queryFn: () => get<IntegrationConfig[]>(`/api/v1/integrations${type ? `?type=${type}` : ''}`),
  });
}

export function useIntegration(id: number) {
  return useQuery({
    queryKey: ['integration', id],
    queryFn: () => get<IntegrationSnapshot>(`/api/v1/integrations/${id}`),
    enabled: id > 0,
    refetchInterval: 60_000,
  });
}
