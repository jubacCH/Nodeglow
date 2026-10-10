'use client';

import Link from 'next/link';
import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface TabItem<K extends string = string> {
  id: K;
  label: ReactNode;
  /** Count badge after the label. */
  count?: number;
  /** Count tone: `down` for open critical things, otherwise neutral. */
  countTone?: 'neutral' | 'down';
  disabled?: boolean;
}

interface TabsProps<K extends string> {
  items: TabItem<K>[];
  value: K;
  onChange: (id: K) => void;
  /** Accessible name of the tab list. */
  label: string;
  /** Prefix for tab/panel ids; panels use `${idBase}-panel-${id}`. */
  idBase?: string;
  className?: string;
}

function Count({ n, tone }: { n: number; tone?: 'neutral' | 'down' }) {
  return (
    <span
      className={cn(
        'num ml-1.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-pill px-1.5 text-micro font-semibold',
        tone === 'down' ? 'bg-down-soft text-down' : 'bg-surface-3 text-fg-2',
      )}
    >
      {n}
    </span>
  );
}

const tabClass = (active: boolean) =>
  cn(
    'relative inline-flex h-[40px] shrink-0 items-center whitespace-nowrap px-3 text-ui font-medium transition-colors',
    'after:absolute after:inset-x-2 after:bottom-[-1px] after:h-[2px] after:rounded-full',
    active ? 'text-fg after:bg-accent' : 'text-fg-2 hover:text-fg after:bg-transparent',
  );

/**
 * ARIA tabs (role=tablist) with arrow-key navigation, for switching panels
 * inside a page. Render the panels with <TabPanel>. For navigation between
 * URLs use <NavTabs>.
 */
export function Tabs<K extends string>({ items, value, onChange, label, idBase, className }: TabsProps<K>) {
  const auto = useId();
  const base = idBase ?? auto;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (e: KeyboardEvent, index: number) => {
    const enabled = items.map((t, i) => (t.disabled ? -1 : i)).filter((i) => i >= 0);
    const pos = enabled.indexOf(index);
    let next: number | undefined;
    if (e.key === 'ArrowRight') next = enabled[(pos + 1) % enabled.length];
    else if (e.key === 'ArrowLeft') next = enabled[(pos - 1 + enabled.length) % enabled.length];
    else if (e.key === 'Home') next = enabled[0];
    else if (e.key === 'End') next = enabled[enabled.length - 1];
    if (next === undefined) return;
    e.preventDefault();
    refs.current[next]?.focus();
    onChange(items[next].id);
  };

  return (
    <div role="tablist" aria-label={label} className={cn('flex gap-1 overflow-x-auto border-b border-border', className)}>
      {items.map((t, i) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => { refs.current[i] = el; }}
            type="button"
            role="tab"
            id={`${base}-tab-${t.id}`}
            aria-selected={active}
            aria-controls={`${base}-panel-${t.id}`}
            tabIndex={active ? 0 : -1}
            disabled={t.disabled}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => move(e, i)}
            className={cn(tabClass(active), 'ng-focus-inset disabled:opacity-50')}
          >
            {t.label}
            {t.count !== undefined && t.count > 0 && <Count n={t.count} tone={t.countTone} />}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ idBase, id, active, children, className }: { idBase: string; id: string; active: boolean; children: ReactNode; className?: string }) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={`${idBase}-panel-${id}`} aria-labelledby={`${idBase}-tab-${id}`} tabIndex={0} className={cn('outline-none', className)}>
      {children}
    </div>
  );
}

export interface NavTabItem {
  href: string;
  label: ReactNode;
  active: boolean;
  count?: number;
  countTone?: 'neutral' | 'down';
}

/**
 * Link tabs for navigation (section sub-nav, detail-page tabs as path
 * segments). A <nav> with aria-current, not an ARIA tablist.
 */
export function NavTabs({ items, label, className }: { items: NavTabItem[]; label: string; className?: string }) {
  return (
    <nav aria-label={label} className={cn('border-b border-border', className)}>
      <ul className="-mb-px flex gap-1 overflow-x-auto [scrollbar-width:none]">
        {items.map((t) => (
          <li key={t.href} className="shrink-0">
            <Link href={t.href} aria-current={t.active ? 'page' : undefined} className={cn(tabClass(t.active), 'ng-focus-inset')}>
              {t.label}
              {t.count !== undefined && t.count > 0 && <Count n={t.count} tone={t.countTone} />}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export interface SegmentOption<K extends string = string> {
  value: K;
  label: ReactNode;
  /** Accessible label when `label` is an icon. */
  ariaLabel?: string;
}

/**
 * Segmented control: a radio group for 2–5 mutually exclusive view options
 * (time range presets, list/map, theme). Arrow keys move the selection.
 */
export function SegmentedControl<K extends string>({
  options, value, onChange, label, size = 'md', className,
}: {
  options: SegmentOption<K>[];
  value: K;
  onChange: (v: K) => void;
  label: string;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    let n: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') n = (i + 1) % options.length;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') n = (i - 1 + options.length) % options.length;
    if (n === null) return;
    e.preventDefault();
    refs.current[n]?.focus();
    onChange(options[n].value);
  };
  return (
    <div role="radiogroup" aria-label={label} className={cn('inline-flex gap-0.5 rounded-[9px] border border-border bg-surface p-[3px]', className)}>
      {options.map((o, i) => {
        const checked = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => { refs.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={o.ariaLabel}
            tabIndex={checked ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              'inline-flex items-center gap-1.5 whitespace-nowrap rounded-[6px] font-medium transition-colors',
              size === 'md' ? 'h-[28px] px-2.5 text-meta' : 'h-[24px] px-2 text-micro',
              checked ? 'bg-surface-3 text-fg' : 'text-fg-2 hover:bg-surface-2 hover:text-fg',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
