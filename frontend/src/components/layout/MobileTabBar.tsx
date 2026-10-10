'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SidePanel } from '@/components/ui/SidePanel';
import { useNavBadges, useShellNav } from './useShellNav';

const tabClass = (active: boolean) =>
  cn(
    'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-[9px] py-1 text-[10px] font-medium leading-none',
    active ? 'text-accent' : 'text-fg-3',
  );

/**
 * Below 760px the rail becomes a bottom tab bar: the five main sections
 * (lib/navigation `mobile: true`) plus "More" with every page.
 */
export function MobileTabBar() {
  const { sections, active, pathname } = useShellNav();
  const badges = useNavBadges();
  const [moreOpen, setMoreOpen] = useState(false);
  const primary = sections.filter((s) => s.mobile);
  const moreActive = !!active && !primary.some((s) => s.id === active.section.id);

  useEffect(() => setMoreOpen(false), [pathname]);

  return (
    <>
      <nav
        aria-label="Sections"
        className="fixed inset-x-0 bottom-0 z-rail hidden h-tabbar items-stretch gap-0.5 border-t border-border bg-rail px-2 pb-[env(safe-area-inset-bottom)] max-[759px]:flex"
      >
        {primary.map((s) => {
          const isActive = active?.section.id === s.id;
          const count = s.badge ? badges[s.badge] : null;
          return (
            <Link
              key={s.id}
              href={s.items[0].href}
              aria-current={isActive ? 'page' : undefined}
              aria-label={count ? `${s.label}, ${count} open` : s.label}
              className={tabClass(isActive)}
            >
              <span className={cn('relative grid h-[28px] w-[40px] place-items-center rounded-[8px]', isActive && 'bg-accent-soft')}>
                <s.icon size={20} strokeWidth={1.7} aria-hidden="true" />
                {!!count && (
                  <span aria-hidden="true" className="num absolute -right-0.5 -top-1 h-[15px] min-w-[15px] rounded-[8px] bg-down px-1 text-center text-[10px] font-semibold leading-[15px] text-on-accent shadow-glow-dot-crit">
                    {count > 99 ? '99+' : count}
                  </span>
                )}
              </span>
              <span aria-hidden="true" className="max-w-full truncate">{s.shortLabel}</span>
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          className={tabClass(moreActive)}
        >
          <span className={cn('grid h-[28px] w-[40px] place-items-center rounded-[8px]', moreActive && 'bg-accent-soft')}>
            <MoreHorizontal size={20} aria-hidden="true" />
          </span>
          <span>More</span>
        </button>
      </nav>

      <SidePanel open={moreOpen} onClose={() => setMoreOpen(false)} title="All pages">
        {sections.map((s) => (
          <section key={s.id} className="border-b border-border py-3 last:border-b-0">
            <h3 className="mb-1 flex items-center gap-2 text-meta font-medium text-fg-2">
              <s.icon size={14} aria-hidden="true" /> {s.label}
            </h3>
            <ul>
              {s.items.map((item) => {
                const isActive = active?.item.id === item.id;
                return (
                  <li key={item.id}>
                    <Link
                      href={item.href}
                      aria-current={isActive ? 'page' : undefined}
                      className={cn(
                        'flex h-[40px] items-center gap-2.5 rounded-ctl px-2 text-ui',
                        isActive ? 'bg-accent-soft text-accent' : 'text-fg hover:bg-surface-2',
                      )}
                    >
                      <item.icon size={16} aria-hidden="true" className={isActive ? '' : 'text-fg-2'} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </SidePanel>
    </>
  );
}
