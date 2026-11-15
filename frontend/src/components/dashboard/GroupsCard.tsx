'use client';

import Link from 'next/link';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusPill } from '@/components/ui/StatusPill';
import { STATE_FILL, describeSegments } from '@/lib/status';
import { describeStates, groupFreshness, stateSegments } from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { HostGroup, SectionError, SiteGroup } from '@/types/dashboard';
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
 * Hosts by site once there are two or more sites (B-14), else grouped by who
 * checks them: "Direct" (this instance) and one group per probe. A silent
 * probe's hosts are "no data", never green, in either view.
 */
export function GroupsCard({ groups, sites, loading, error, className }: {
  groups: HostGroup[] | null | undefined;
  sites?: SiteGroup[] | null;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  if (sites && sites.length >= 2) {
    return <SitesView sites={sites} loading={loading} error={error} className={className} />;
  }
  return <CheckerView groups={groups} loading={loading} error={error} className={className} />;
}

/** "Hosts by site": one row per site, linked to the host list filtered to it. */
function SitesView({ sites, loading, error, className }: {
  sites: SiteGroup[];
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const total = sites.reduce((a, s) => a + s.host_count, 0);
  const empty = sites.filter((s) => s.host_count === 0 && s.probes.length === 0).length;
  const visible = sites.filter((s) => s.host_count > 0 || s.probes.length > 0);
  return (
    <DashboardCard
      id="dash-groups"
      title="Sites"
      meta="hosts by site"
      loading={loading}
      error={error}
      className={className}
      actions={<span className="num">{total} host{total === 1 ? '' : 's'}</span>}
    >
      {visible.length === 0 ? (
        <EmptyState compact title="No hosts yet" />
      ) : (
        <>
          <ul>
            {visible.map((s) => {
              const silent = s.probes.filter((p) => !p.fresh);
              const probeText = s.probes.length === 0
                ? (s.is_default ? 'default site' : 'no probe')
                : s.probes.length === 1 ? `via ${s.probes[0].name}` : `${s.probes.length} probes`;
              return (
                <li key={s.id} className="border-b border-border py-3 first:pt-0 last:border-b-0 last:pb-0">
                  <Link href={`/hosts?site=${s.id}`} className="block w-full rounded-ctl hover:bg-hover">
                    <span className="mb-2 flex items-baseline justify-between gap-2">
                      <span className="min-w-0 truncate">
                        <b className="font-medium text-fg">{s.name}</b>{' '}
                        <span className="text-meta text-fg-2">{probeText}</span>
                      </span>
                      <span className="num font-display text-[1.4286rem] font-medium leading-[1.1] tracking-[-0.04em] text-fg">{s.host_count}</span>
                    </span>
                    <StateBar byState={s.by_state} label={`${s.name}: ${describeSegments(stateSegments(s.by_state))}`} />
                    <span className="mt-[7px] flex flex-wrap items-center gap-2 text-meta text-fg-2">
                      {silent.length > 0 && (
                        <StatusPill status="unknown" size="sm">
                          {silent.length === 1 ? `${silent[0].name} silent` : `${silent.length} probes silent`}
                        </StatusPill>
                      )}
                      <span>{describeStates(s.by_state)}</span>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
          <p className="mt-auto pt-3.5 text-meta text-fg-3">
            A host is in the site of its probe unless it has its own.
            {empty > 0 && ` ${empty} empty site${empty === 1 ? '' : 's'} hidden.`}{' '}
            <Link href="/sites" className="text-accent hover:text-accent-hover">Manage sites</Link>
          </p>
        </>
      )}
    </DashboardCard>
  );
}

function CheckerView({ groups, loading, error, className }: {
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
          <p className="mt-auto pt-3.5 text-meta text-fg-3">
            Grouped by who checks the hosts.
            {unusedProbes > 0 && ` ${unusedProbes} probe${unusedProbes === 1 ? '' : 's'} without hosts hidden.`}{' '}
            <Link href="/sites" className="text-accent hover:text-accent-hover">Group by site</Link>
          </p>
        </>
      ) : null}
    </DashboardCard>
  );
}
