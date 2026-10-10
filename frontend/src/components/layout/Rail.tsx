'use client';

import Link from 'next/link';
import { cn } from '@/lib/utils';
import type { NavSection } from '@/lib/navigation';
import { Tooltip } from '@/components/ui/Tooltip';
import { Popover } from '@/components/ui/Popover';
import { Lockup, NodeglowMark } from '@/components/ui/NodeglowMark';
import { useFeatures } from '@/hooks/queries/useFeatures';
import { useNavBadges, useShellNav } from './useShellNav';

/** Public source of this build — AGPL-3.0 section 13 asks network users to get it. */
export const SOURCE_URL = 'https://github.com/jubacCH/Nodeglow';

/** Count badge on a rail icon. Critical counts glow (E3). */
export function NavCountBadge({ count, className }: { count: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'num absolute right-[5px] top-[5px] h-[15px] min-w-[15px] rounded-[8px] bg-down px-1 text-center text-[10px] font-semibold leading-[15px] text-on-accent shadow-glow-dot-crit',
        className,
      )}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

function AboutDetails() {
  const { data } = useFeatures();
  const enterprise = data?.edition === 'enterprise';
  return (
    <dl className="mt-3.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-t border-border pt-3 text-meta">
      <dt className="text-fg-3">Edition</dt>
      <dd className="text-fg">{data ? (enterprise ? 'Enterprise' : 'Community') : '—'}</dd>
      <dt className="text-fg-3">License</dt>
      <dd className="text-fg">
        AGPL-3.0{enterprise ? ' core · ee/ under the Nodeglow Enterprise License' : ''}
      </dd>
      <dt className="text-fg-3">Source</dt>
      <dd>
        <a href={SOURCE_URL} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          github.com/jubacCH/Nodeglow
        </a>
      </dd>
    </dl>
  );
}

function AboutPopover() {
  return (
    <Popover
      label="About Nodeglow"
      placement="right-start"
      className="w-[320px] p-4"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className="mb-3 grid h-[40px] w-[40px] place-items-center rounded-card text-fg hover:bg-surface-2"
        >
          <NodeglowMark size={30} />
          <span className="sr-only">About Nodeglow</span>
        </button>
      )}
    >
      <Lockup size={40} wordmarkClassName="text-[22px]" />
      <p className="mt-3 text-meta leading-normal text-fg-2">
        Infrastructure monitoring. Only what needs attention glows; no light means no data.
      </p>
      <AboutDetails />
    </Popover>
  );
}

function RailLink({ section, active, count }: { section: NavSection; active: boolean; count: number | null }) {
  const label = count ? `${section.label} · ${count} open` : section.label;
  return (
    <Tooltip content={label} side="right" asLabel>
      <Link
        href={section.items[0].href}
        aria-current={active ? 'page' : undefined}
        className={cn(
          'relative grid h-[40px] w-[40px] place-items-center rounded-[9px] transition-colors duration-150',
          active ? 'bg-accent-soft text-accent' : 'text-fg-3 hover:bg-surface-2 hover:text-fg',
        )}
      >
        <section.icon size={20} strokeWidth={1.7} aria-hidden="true" />
        {!!count && <NavCountBadge count={count} />}
      </Link>
    </Tooltip>
  );
}

/** Slim left icon rail (desktop and tablet, ≥760px). */
export function Rail() {
  const { sections, active } = useShellNav();
  const badges = useNavBadges();
  const main = sections.filter((s) => s.placement === 'main');
  const bottom = sections.filter((s) => s.placement === 'bottom');
  const badgeOf = (s: NavSection) => (s.badge ? badges[s.badge] : null);

  return (
    <aside
      aria-label="Primary"
      className="fixed inset-y-0 left-0 z-rail flex w-rail flex-col items-center gap-1 border-r border-border bg-rail py-3 max-[759px]:hidden"
    >
      <AboutPopover />
      <nav aria-label="Sections" className="flex flex-1 flex-col items-center gap-1">
        {main.map((s) => (
          <RailLink key={s.id} section={s} active={active?.section.id === s.id} count={badgeOf(s)} />
        ))}
        <div className="flex-1" />
        {bottom.map((s) => (
          <RailLink key={s.id} section={s} active={active?.section.id === s.id} count={badgeOf(s)} />
        ))}
      </nav>
    </aside>
  );
}
