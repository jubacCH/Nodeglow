import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { IntegrationConfig, IntegrationSnapshot } from '@/types';

export function useIntegrations(
  type?: string,
  opts: { enabled?: boolean; refetchInterval?: number | false } = {},
) {
  return useQuery({
    queryKey: ['integrations', type],
    queryFn: () => get<IntegrationConfig[]>(`/api/v1/integrations${type ? `?type=${type}` : ''}`),
    enabled: opts.enabled ?? true,
    refetchInterval: opts.refetchInterval,
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
