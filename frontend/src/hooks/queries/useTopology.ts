import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

interface TopologyNode {
  id: number;
  name: string;
  hostname: string;
  status: 'up' | 'down';
  check_type: string;
  source: string;
  maintenance: boolean;
}

interface TopologyEdge {
  source: number;
  target: number;
}

interface TopologyData {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export function useTopology() {
  return useQuery({
    queryKey: ['topology'],
    queryFn: () => get<TopologyData>('/api/v1/topology'),
    refetchInterval: 30_000,
  });
}
