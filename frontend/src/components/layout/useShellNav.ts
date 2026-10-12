'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { useNavCounts, useSummary } from '@/hooks/queries/useDashboard';
import { useFeatures } from '@/hooks/queries/useFeatures';
import { useIsAdmin, useIsOperator } from '@/stores/auth';
import { findActive, visibleSections, type NavAudience, type NavBadge } from '@/lib/navigation';

/**
 * Badge counts for the navigation (null = unknown, not 0). Incidents come
 * from /api/v2/summary — the same definition the dashboard uses ("open" =
 * open or acknowledged).
 */
export function useNavBadges(): Record<NavBadge, number | null> {
  const { data: summary } = useSummary();
  const { data: navCounts } = useNavCounts();
  return {
    incidents: summary?.incidents?.open ?? null,
    discovery: navCounts ? navCounts.tasks ?? 0 : null,
  };
}

/** Who is looking: operator flag and installed features (operator-only, ee items). */
export function useNavAudience(): NavAudience {
  const isOperator = useIsOperator();
  const { data: features } = useFeatures(isOperator);
  return { isOperator, installed: features?.installed };
}

/**
 * Sections visible to the current user and the active section/item for the
 * current URL. Uses useSearchParams: render inside a <Suspense> boundary.
 */
export function useShellNav() {
  const pathname = usePathname();
  const search = useSearchParams();
  const isAdmin = useIsAdmin();
  const audience = useNavAudience();
  const sections = visibleSections(isAdmin, audience);
  const active = findActive(pathname, search, sections) ?? findActive(pathname, search);
  return { sections, active, pathname };
}
