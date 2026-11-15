import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

/** GET /api/v1/sites (B-14). Host and probe counts are by effective site. */
export interface Site {
  id: number;
  name: string;
  is_default: boolean;
  host_count: number;
  probe_count: number;
  created_at: string | null;
}

export const SITES_KEY = ['sites'] as const;

export function useSites() {
  return useQuery({
    queryKey: SITES_KEY,
    queryFn: () => get<Site[]>('/api/v1/sites'),
    staleTime: 30_000,
  });
}

/** Sites matter in the UI only once there is more than the default one. */
export function hasSeveralSites(sites: Site[] | undefined): boolean {
  return (sites?.length ?? 0) > 1;
}
