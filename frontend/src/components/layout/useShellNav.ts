'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { useNavCounts, useSystemSummary } from '@/hooks/queries/useDashboard';
import { useIsAdmin } from '@/stores/auth';
import { findActive, visibleSections, type NavBadge } from '@/lib/navigation';

/** Badge counts for the navigation (null = unknown, not 0). */
export function useNavBadges(): Record<NavBadge, number | null> {
  const { data: summary } = useSystemSummary();
  const { data: navCounts } = useNavCounts();
  return {
    incidents: summary?.incidents?.open ?? null,
    discovery: navCounts ? navCounts.tasks ?? 0 : null,
  };
}

/**
 * Sections visible to the current user and the active section/item for the
 * current URL. Uses useSearchParams: render inside a <Suspense> boundary.
 */
export function useShellNav() {
  const pathname = usePathname();
  const search = useSearchParams();
  const isAdmin = useIsAdmin();
  const sections = visibleSections(isAdmin);
  const active = findActive(pathname, search, sections) ?? findActive(pathname, search);
  return { sections, active, pathname };
}
