'use client';

import { StatusDot } from '@/components/ui/StatusDot';
import { cn } from '@/lib/utils';
import { HOST_STATES, HOST_STATE_LABEL, hostStatusProp, type HostState } from './hostState';

interface StateFilterChipsProps {
  value: HostState | null;
  onChange: (state: HostState | null) => void;
  /** Hosts per state; undefined while loading (no zeros are shown then). */
  counts?: Record<HostState, number>;
  total?: number;
}

/** "All · Up · Degraded …" filter, one state at a time, with counts. */
export function StateFilterChips({ value, onChange, counts, total }: StateFilterChipsProps) {
  const chip = (active: boolean) =>
    cn(
      'inline-flex h-[28px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-pill border px-2.5 text-meta font-medium transition-colors',
      active ? 'border-accent/40 bg-accent-soft text-accent' : 'border-border bg-surface text-fg-2 hover:bg-surface-2 hover:text-fg',
    );
  const count = (n: number | undefined) =>
    n === undefined ? null : <span className="num text-fg-3">{n}</span>;
  return (
    <div role="group" aria-label="Filter by state" className="flex flex-wrap items-center gap-1.5">
      <button type="button" aria-pressed={value === null} className={chip(value === null)} onClick={() => onChange(null)}>
        All {count(total)}
      </button>
      {HOST_STATES.map((s) => {
        const n = counts?.[s];
        return (
          <button
            key={s}
            type="button"
            aria-pressed={value === s}
            className={cn(chip(value === s), n === 0 && value !== s && 'opacity-60')}
            onClick={() => onChange(value === s ? null : s)}
          >
            <StatusDot status={hostStatusProp(s)} size="sm" glow={false} label="" />
            {HOST_STATE_LABEL[s]} {count(n)}
          </button>
        );
      })}
    </div>
  );
}
