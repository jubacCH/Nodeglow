import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { AiStatus } from '@/lib/ai';

export type { AiStatus } from '@/lib/ai';
export { aiUnavailableMessage } from '@/lib/ai';

export const AI_STATUS_KEY = ['ai-status'] as const;

export function useAiStatus(enabled = true) {
  return useQuery<AiStatus>({
    queryKey: AI_STATUS_KEY,
    queryFn: () => get<AiStatus>('/api/v1/ai/status'),
    staleTime: 60_000,
    enabled,
  });
}
