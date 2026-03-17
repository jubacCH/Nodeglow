import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

interface AuditEntry {
  id: number;
  timestamp: string | null;
  user_id: number | null;
  username: string | null;
  action: string;
  target_type: string | null;
  target_id: number | null;
  target_name: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
}

interface AuditResponse {
  total: number;
  logs: AuditEntry[];
}

export function useAudit(params?: { action?: string; user?: string; limit?: number; offset?: number }) {
  const qs = new URLSearchParams();
  if (params?.action) qs.set('action', params.action);
  if (params?.user) qs.set('user', params.user);
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.offset) qs.set('offset', String(params.offset));
  const query = qs.toString();

  return useQuery({
    queryKey: ['audit', params],
    queryFn: () => get<AuditResponse>(`/api/v1/audit${query ? `?${query}` : ''}`),
    refetchInterval: 30_000,
  });
}
