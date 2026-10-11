import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { SupportState } from '@/lib/tenants';
import { useFeatures } from './useFeatures';
import { useAuthStore } from '@/stores/auth';

export const SUPPORT_ACCESS_KEY = ['support-access'] as const;

/**
 * Support access of the current tenant (GET /api/support-access). Only asked
 * where the multi-tenancy package is installed (the route exists only there).
 */
export function useSupportAccess() {
  const user = useAuthStore((s) => s.user);
  const { data: features } = useFeatures(!!user);
  const installed = features?.installed?.multi_tenancy === true;
  return useQuery<SupportState>({
    queryKey: SUPPORT_ACCESS_KEY,
    queryFn: () => get<SupportState>('/api/support-access'),
    enabled: !!user && installed,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}
