'use client';

import Link from 'next/link';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { NavSection } from '@/lib/navigation';
import { Tooltip } from '@/components/ui/Tooltip';
import { Popover } from '@/components/ui/Popover';
import { Lockup, NodeglowMark } from '@/components/ui/NodeglowMark';
import { useFeatures } from '@/hooks/queries/useFeatures';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useThemeStore } from '@/stores/theme';
import { useNavBadges, useShellNav } from './useShellNav';

/** Public source of this build — AGPL-3.0 section 13 asks network users to get it. */
export const SOURCE_URL = 'https://github.com/jubacCH/Nodeglow';

/** The rail shows labels from this width on, unless the user collapsed it (see globals.css). */
export const WIDE_RAIL_QUERY = '(min-width: 1100px)';

/**
 * Count badge on a rail entry. Soft fill with the status text colour (white on
 * the status colour fails contrast); critical counts keep their glow (E3).
 */
export function NavCountBadge({ count, className }: { count: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'num h-[17px] min-w-[17px] rounded-pill bg-down-soft px-1.5 text-center text-[10px] font-semibold leading-[17px] text-down shadow-glow-dot-crit',
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

/** Brand at the top of the rail: the lockup when wide, the mark when narrow. Opens About. */
function AboutPopover({ wide }: { wide: boolean }) {
  return (
    <Popover
      label="About Nodeglow"
      placement="right-start"
      className="w-[320px] p-4"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className={cn(
            'mb-3 flex h-[44px] items-center rounded-ctl text-fg hover:bg-surface-2',
            wide ? 'w-full px-2.5' : 'w-[44px] justify-center',
          )}
        >
          {wide ? <Lockup size={24} /> : <NodeglowMark size={30} />}
          <span className="sr-only">About Nodeglow</span>
        </button>
      )}
    >
      <Lockup size={28} />
      <p className="mt-3 text-meta leading-normal text-fg-2">
        Infrastructure monitoring. Only what needs attention glows; no light means no data.
      </p>
      <AboutDetails />
    </Popover>
  );
}

function RailLink({ section, active, count, wide }: { section: NavSection; active: boolean; count: number | null; wide: boolean }) {
  const look = active ? 'bg-accent-soft text-accent font-semibold' : 'text-fg-2 hover:bg-surface-2 hover:text-fg';
  const icon = <section.icon size={20} strokeWidth={1.7} aria-hidden="true" className="shrink-0" />;

  if (wide) {
    return (
      <Link
        href={section.items[0].href}
        aria-current={active ? 'page' : undefined}
        className={cn('flex h-[42px] items-center gap-3 rounded-ctl px-3 text-ui transition-colors duration-150', look)}
      >
        {icon}
        <span className="min-w-0 flex-1 truncate">{section.label}</span>
        {!!count && (
          <>
            <NavCountBadge count={count} />
            <span className="sr-only">, {count} open</span>
          </>
        )}
      </Link>
    );
  }

  const label = count ? `${section.label} · ${count} open` : section.label;
  return (
    <Tooltip content={label} side="right" asLabel>
      <Link
        href={section.items[0].href}
        aria-current={active ? 'page' : undefined}
        className={cn('relative grid h-[42px] w-[42px] place-items-center rounded-ctl transition-colors duration-150', look)}
      >
        {icon}
        {!!count && <NavCountBadge count={count} className="absolute -right-1 -top-1" />}
      </Link>
    </Tooltip>
  );
}

/** Collapse / expand the labelled rail. Only offered where the wide rail fits. */
function CollapseButton({ collapsed, onToggle, wide }: { collapsed: boolean; onToggle: () => void; wide: boolean }) {
  const Icon = collapsed ? PanelLeftOpen : PanelLeftClose;
  const label = collapsed ? 'Expand navigation' : 'Collapse navigation';
  const button = (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      aria-label={wide ? undefined : label}
      className={cn(
        'flex h-[42px] items-center gap-3 rounded-ctl text-ui text-fg-3 transition-colors duration-150 hover:bg-surface-2 hover:text-fg',
        wide ? 'w-full px-3' : 'w-[42px] justify-center',
      )}
    >
      <Icon size={20} strokeWidth={1.7} aria-hidden="true" className="shrink-0" />
      {wide && <span>Collapse</span>}
    </button>
  );
  return wide ? button : <Tooltip content={label} side="right">{button}</Tooltip>;
}

/**
 * Left rail (≥760px). From 1100px it shows labels (concept F) unless the user
 * collapsed it; the width comes from --ng-rail-w, which globals.css switches
 * via data-rail on <html>.
 */
export function Rail() {
  const { sections, active } = useShellNav();
  const badges = useNavBadges();
  const collapsed = useThemeStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useThemeStore((s) => s.toggleSidebar);
  const roomy = useMediaQuery(WIDE_RAIL_QUERY);
  const wide = roomy && !collapsed;
  const main = sections.filter((s) => s.placement === 'main');
  const bottom = sections.filter((s) => s.placement === 'bottom');
  const badgeOf = (s: NavSection) => (s.badge ? badges[s.badge] : null);

  return (
    <aside
      aria-label="Primary"
      className={cn(
        'fixed inset-y-0 left-0 z-rail flex w-rail flex-col gap-1 border-r border-border bg-rail py-3 transition-[width] duration-200 max-[759px]:hidden',
        wide ? 'items-stretch px-3' : 'items-center',
      )}
    >
      <AboutPopover wide={wide} />
      <nav aria-label="Sections" className={cn('flex flex-1 flex-col gap-1', wide ? 'items-stretch' : 'items-center')}>
        {main.map((s) => (
          <RailLink key={s.id} section={s} active={active?.section.id === s.id} count={badgeOf(s)} wide={wide} />
        ))}
        <div className="flex-1" />
        {bottom.map((s) => (
          <RailLink key={s.id} section={s} active={active?.section.id === s.id} count={badgeOf(s)} wide={wide} />
        ))}
      </nav>
      {roomy && <CollapseButton collapsed={collapsed} onToggle={toggleSidebar} wide={wide} />}
    </aside>
  );
}
