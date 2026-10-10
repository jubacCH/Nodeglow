'use client';

import { NavTabs } from '@/components/ui/Tabs';
import { useNavBadges, useShellNav } from './useShellNav';

/**
 * Secondary navigation of the active section, as link tabs at the top of the
 * content area. Driven by lib/navigation.ts; hidden for single-page sections.
 */
export function SubNav() {
  const { sections, active } = useShellNav();
  const badges = useNavBadges();
  if (!active) return null;
  const section = sections.find((s) => s.id === active.section.id);
  if (!section || section.items.length < 2) return null;
  return (
    <NavTabs
      label={section.label}
      className="mb-6"
      items={section.items.map((item) => {
        const count = item.badge ? badges[item.badge] : null;
        return {
          href: item.href,
          label: item.label,
          active: item.id === active.item.id,
          count: count ?? undefined,
          countTone: item.badge === 'incidents' ? 'down' : 'neutral',
        };
      })}
    />
  );
}
