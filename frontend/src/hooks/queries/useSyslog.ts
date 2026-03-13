import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { SyslogMessage } from '@/types';

export function useSyslog(filters: { severity?: string; host?: string; limit?: number }) {
  return useQuery({
    queryKey: ['syslog', filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.severity) params.set('severity', filters.severity);
      if (filters.host) params.set('host', filters.host);
      if (filters.limit) params.set('limit', String(filters.limit));
      const qs = params.toString();
      return get<SyslogMessage[]>(`/api/v1/syslog${qs ? `?${qs}` : ''}`);
    },
    refetchInterval: 10_000,
  });
}
