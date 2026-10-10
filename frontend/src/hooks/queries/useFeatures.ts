import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { Features } from '@/lib/features';

export type { Features, FeatureName, LicenseSummary, LicenseState } from '@/lib/features';
export {
  hasFeature, lacksFeature, hasAnyAiFeature, ENTERPRISE_NOTES, enterpriseNote, licenseBanner,
} from '@/lib/features';

export const FEATURES_KEY = ['features'] as const;

/**
 * Edition + feature flags. They change with a deploy or a license change
 * (Settings → License invalidates this query), so cache them long.
 */
export function useFeatures(enabled = true) {
  return useQuery<Features>({
    queryKey: FEATURES_KEY,
    queryFn: () => get<Features>('/api/v2/features'),
    staleTime: 10 * 60_000,
    enabled,
  });
}
