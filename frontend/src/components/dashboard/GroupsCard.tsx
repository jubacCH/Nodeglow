'use client';

import { EmptyState } from '@/components/ui/EmptyState';
import { StatusPill } from '@/components/ui/StatusPill';
import { Tag } from '@/components/ui/Tag';
import { STATE_FILL, describeSegments } from '@/lib/status';
import { describeStates, groupFreshness, stateSegments } from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { HostGroup, SectionError } from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';

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

/**
 * Hosts grouped by who checks them: "Direct" (this instance) and one group
 * per probe. These are not sites — Nodeglow has no site model yet — and the
 * card says so. A silent probe's group is "no data", never green.
 */
export function GroupsCard({ groups, loading, error, className }: {
  groups: HostGroup[] | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const { now, open } = useDashboardCtx();
  const total = (groups ?? []).reduce((a, g) => a + g.host_count, 0);
  const visible = (groups ?? []).filter((g) => g.kind === 'direct' || g.host_count > 0 || g.fresh === false);
  const unusedProbes = (groups ?? []).length - visible.length;

  return (
    <DashboardCard
      id="dash-groups"
      title="Groups"
      meta="by checker"
      loading={loading}
      error={error}
      className={className}
      actions={groups && <span className="num">{total} host{total === 1 ? '' : 's'}</span>}
    >
      {groups && visible.length === 0 ? (
        <EmptyState compact title="No hosts yet" />
      ) : groups ? (
        <>
          <ul>
            {visible.map((g) => {
              const f = groupFreshness(g, now);
              const silent = g.kind === 'probe' && g.fresh === false;
              return (
                <li key={`${g.kind}-${g.id ?? 'x'}`} className="border-b border-border py-3 first:pt-0 last:border-b-0 last:pb-0">
                  <button type="button" onClick={() => open({ kind: 'group', group: g })} className="block w-full rounded-ctl text-left hover:bg-hover">
                    <span className="mb-2 flex items-baseline justify-between gap-2">
                      <span className="min-w-0 truncate">
                        <b className="font-medium text-fg">{g.name}</b>{' '}
                        <span className="text-meta text-fg-2">{g.kind === 'direct' ? 'checked by this instance' : 'via remote probe'}</span>
                      </span>
                      <span className="num font-display text-[1.4286rem] font-medium leading-[1.1] tracking-[-0.04em] text-fg">{g.host_count}</span>
                    </span>
                    <StateBar
                      byState={g.by_state}
                      label={`${g.name}: ${describeSegments(stateSegments(g.by_state))}`}
                      className={silent ? 'opacity-90' : undefined}
                    />
                    <span className={cn('mt-[7px] flex flex-wrap items-center gap-2 text-meta', silent ? 'text-fg-2' : 'text-fg-2')}>
                      {silent && <StatusPill status="unknown" size="sm">Probe silent</StatusPill>}
                      <span>{silent ? f.text : g.kind === 'direct' ? describeStates(g.by_state) : f.text}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="mt-auto flex flex-wrap items-center gap-2 pt-3.5 text-meta text-fg-3">
            <Tag planned>Sites · Planned</Tag>
            Grouped by who checks the hosts, not by location.
            {unusedProbes > 0 && ` ${unusedProbes} probe${unusedProbes === 1 ? '' : 's'} without hosts hidden.`}
          </p>
        </>
      ) : null}
    </DashboardCard>
  );
}
