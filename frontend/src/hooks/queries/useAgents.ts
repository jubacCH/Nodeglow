import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { Agent } from '@/types';

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: () => get<Agent[]>('/api/v1/agents'),
    refetchInterval: 15_000,
  });
}
