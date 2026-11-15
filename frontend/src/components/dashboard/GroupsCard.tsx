'use client';

import { STATE_FILL } from '@/lib/status';
import { stateSegments } from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { HostGroup } from '@/types/dashboard';

/** Thin state bar (E3 `.sbar`); unknown is hatched, never a colour. */
export function StateBar({ byState, label, className }: { byState: HostGroup['by_state']; label: string; className?: string }) {
  const segs = stateSegments(byState);
  return (
    <div role="img" aria-label={label} className={cn('flex h-1.5 gap-[2px] overflow-hidden rounded-[3px] bg-surface-2', className)}>
      {segs.map((s) => (
        <i key={s.state} className={cn('block h-full', STATE_FILL[s.state])} style={{ flex: s.count }} />
      ))}
    </div>
  );
}
