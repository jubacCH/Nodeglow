import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { Agent } from '@/types';
import { whileLive } from '@/stores/websocket';

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: () => get<Agent[]>('/api/v1/agents'),
    // CPU/mem/disk arrive live over the WebSocket; poll slower while it is up.
    refetchInterval: whileLive(15_000, 60_000),
  });
}
