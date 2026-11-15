'use client';

import Link from 'next/link';
import { useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { DashboardCard, useDashboardCtx } from '@/components/dashboard/DashboardCard';
import { StateBar } from '@/components/dashboard/GroupsCard';
import { describeStates, fmtNum, fmtPct, formatWhen } from '@/lib/dashboard';
import { groupSummary, hourlySyslog, rankGroups, rankSites, siteAsGroup, siteCheckers, type HeroCounts } from '@/lib/overview';
import { STATE_FILL } from '@/lib/status';
import { cn } from '@/lib/utils';
import type {
  AvailabilitySection, HealthSection, HostGroup, IncidentsSection, SectionError, SinceLastVisitSection, SiteGroup, SyslogSection,
} from '@/types/dashboard';

// ── Head ─────────────────────────────────────────────────────────────────

/** Concept F head: the state of things in one sentence, with the honest host split under it. */
export function OverviewHero({ open, counts, health, slv, updated }: {
  open: number | null;
  counts: HeroCounts | null;
  health: HealthSection | null | undefined;
  slv: SinceLastVisitSection | null | undefined;
  updated: string;
}) {
  const { now } = useDashboardCtx();
  return (
    <section aria-labelledby="overview-title" className="mb-5 flex flex-wrap items-end justify-between gap-x-5 gap-y-3">
      <div className="min-w-0">
        <p className="mb-1.5 text-meta text-fg-2">{updated}</p>
        <h1 id="overview-title" className="font-display text-[clamp(26px,3vw,38px)] font-medium leading-tight tracking-[-0.04em] text-fg">
          {open === null ? 'Overview' : open === 0 ? 'No incident needs you' : (
            <>
              {open}{' '}
              <span aria-hidden="true" className="inline-grid h-[1.05em] place-items-center rounded-pill bg-down-soft px-[0.3em] align-[-0.12em] text-down">
                <TriangleAlert className="h-[0.58em] w-[0.58em]" />
              </span>{' '}
              {open === 1 ? 'incident needs' : 'incidents need'} you
            </>
          )}
        </h1>
        {counts && health && (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1.5 text-ui text-fg-2">
            <StateBar byState={health.counts} label={describeStates(health.counts)} className="h-2.5 w-[180px] rounded-pill" />
            <span>
              <b className="num font-semibold text-fg">{counts.healthy}</b> of {counts.total} hosts healthy
              {' · '}<b className="num font-semibold text-fg">{counts.issues}</b> with issues
              {' · '}<b className="num font-semibold text-fg">{counts.noData}</b> without data
              {counts.maintenance > 0 && <>{' · '}<b className="num font-semibold text-fg">{counts.maintenance}</b> in maintenance</>}
            </span>
          </div>
        )}
      </div>
      {slv && (
        <Link
          href="/changes"
          className="inline-flex h-[40px] items-center gap-2 rounded-pill bg-surface px-4 text-ui text-fg-2 shadow-card hover:text-fg"
        >
          {slv.fallback ? 'Last 24 h' : <>Since <b className="font-medium text-fg">{formatWhen(slv.since, now)}</b></>}
          <span className="text-fg-3" aria-hidden="true">·</span>
          <span><b className="num font-medium text-fg">{slv.total}</b> change{slv.total === 1 ? '' : 's'}</span>
        </Link>
      )}
    </section>
  );
}

// ── At a glance ──────────────────────────────────────────────────────────

function Spark({ values, label, highlightLast = true }: { values: number[]; label: string; highlightLast?: boolean }) {
  if (values.length === 0) return null;
  const max = Math.max(...values, 1);
  const w = 4;
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${values.length * w} 26`} preserveAspectRatio="none" className="mt-2 block h-[26px] w-full">
      {values.map((v, i) => {
        const h = (v / max) * 26;
        const last = highlightLast && i === values.length - 1;
        return <rect key={i} x={i * w + 0.5} y={26 - h} width={w - 1} height={h} rx={1} className={cn('fill-accent', !last && 'opacity-55')} />;
      })}
    </svg>
  );
}

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-[86px] flex-col justify-between rounded-[18px] bg-surface-2 p-3.5">
      <span className="text-meta text-fg-2">{label}</span>
      {children}
    </div>
  );
}

const Value = ({ children, unit }: { children: React.ReactNode; unit?: React.ReactNode }) => (
  <span className="font-display text-[22px] font-medium tracking-[-0.03em] text-fg">
    {children}
    {unit && <span className="ml-1 font-sans text-meta tracking-normal text-fg-2">{unit}</span>}
  </span>
);

/** Four figures with a sparkline where there is a history. No big chart (concept F). */
export function AtAGlance({ incidents, syslog, availability, health, loading, error, className }: {
  incidents: IncidentsSection | null | undefined;
  syslog: SyslogSection | null | undefined;
  availability: AvailabilitySection | null | undefined;
  health: HealthSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const perDay = incidents?.per_day ?? [];
  const hourly = syslog ? hourlySyslog(syslog) : [];
  const t = health?.totals;
  const reporting = t?.with_current_data ?? null;
  const counted = t ? t.hosts : null;
  const noData = health?.counts.unknown ?? 0;
  return (
    <DashboardCard id="dash-glance" title="At a glance" loading={loading} error={error} className={className}
      actions={<Link href="/digest" className="text-ui font-medium text-accent hover:text-accent-hover">Reports</Link>}
    >
      <div className="grid grid-cols-4 gap-2.5 max-[1279px]:grid-cols-2">
        <Tile label="Opened per day · 14 days">
          <Value unit={incidents ? `${incidents.counts.critical} critical open` : undefined}>{incidents ? perDay.at(-1)?.count ?? 0 : '—'}</Value>
          <Spark values={perDay.map((d) => d.count)} label={`Incidents opened per day, last ${perDay.length} days: ${perDay.map((d) => d.count).join(', ')}`} />
        </Tile>
        <Tile label="Syslog now">
          {syslog && syslog.receiving ? (
            <>
              <Value unit={`/min${syslog.usual ? ` · usual ${fmtNum(syslog.usual.low_per_min)}–${fmtNum(syslog.usual.high_per_min)}` : ''}`}>{fmtNum(syslog.current_per_min)}</Value>
              <Spark values={hourly} label={`Syslog messages per minute, hourly over 24 hours: ${hourly.join(', ')}`} />
            </>
          ) : (
            <span className="text-meta text-fg-3">{syslog ? 'Nothing received in 24 h' : '—'}</span>
          )}
        </Tile>
        <Tile label={`Availability · ${availability?.window_days ?? 30} days`}>
          <Value unit="%">{availability ? fmtPct(availability.pct) : '—'}</Value>
          <span className={cn('text-meta', availability?.status === 'below_target' ? 'text-down' : 'text-fg-2')}>
            {availability ? `Target ${fmtPct(availability.target_pct)} %` : ''}
          </span>
        </Tile>
        <Tile label="Reporting">
          <Value unit={counted !== null ? `/ ${counted} · ${noData} no data` : undefined}>{reporting ?? '—'}</Value>
          {reporting !== null && counted ? (
            <span aria-hidden="true" className="mt-2 flex h-1.5 overflow-hidden rounded-pill">
              <i className="block h-full bg-ok" style={{ width: `${(reporting / counted) * 100}%` }} />
              <i className="ng-hatch block h-full flex-1" />
            </span>
          ) : null}
        </Tile>
      </div>
    </DashboardCard>
  );
}

// ── Hosts by group ───────────────────────────────────────────────────────

const GROUP_CAP = 6;

/** Row layout shared by the checker and the site view. */
const ROW = 'grid w-full grid-cols-[minmax(0,170px)_minmax(0,1fr)_auto] items-center gap-4 rounded-[14px] px-3 py-2.5 text-left hover:bg-surface-2 max-[759px]:grid-cols-[minmax(0,1fr)_auto] max-[759px]:gap-y-1.5';
const BAR = 'h-3.5 rounded-pill max-[759px]:col-span-2 max-[759px]:row-start-2';

function StateLegend() {
  return (
    <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-micro text-fg-3" aria-hidden="true">
      {(['ok', 'degraded', 'warning', 'down', 'maint'] as const).map((s) => (
        <span key={s} className="inline-flex items-center gap-1.5"><i className={cn('h-2 w-2 rounded-chip', STATE_FILL[s])} />{s === 'ok' ? 'Healthy' : s === 'maint' ? 'Maintenance' : s[0].toUpperCase() + s.slice(1)}</span>
      ))}
      <span className="inline-flex items-center gap-1.5"><i className="ng-hatch h-2 w-2 rounded-chip" />No data</span>
    </p>
  );
}

/**
 * Hosts by site once there are two or more sites (B-14), else grouped by who
 * checks them (Direct, one group per probe). One row each with a proportion
 * bar and the counts as text, worst first. A silent probe's hosts are hatched
 * as no data in either view, never green.
 */
export function HostsByGroup({ groups, sites, loading, error, className }: {
  groups: HostGroup[] | null | undefined;
  sites?: SiteGroup[] | null;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  if (sites && sites.length >= 2) {
    return <HostsBySite sites={sites} loading={loading} error={error} className={className} />;
  }
  return <HostsByChecker groups={groups} loading={loading} error={error} className={className} />;
}

function HostsBySite({ sites, loading, error, className }: {
  sites: SiteGroup[];
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const [all, setAll] = useState(false);
  const ranked = rankSites(sites);
  const shown = all ? ranked : ranked.slice(0, GROUP_CAP);
  const total = ranked.reduce((a, s) => a + s.host_count, 0);
  const hidden = sites.length - ranked.length;
  return (
    <DashboardCard id="dash-groups" title="Hosts by site" meta={`${total} hosts · own site, else the probe's`} loading={loading} error={error} className={className}>
      <ul className="flex flex-col gap-0.5">
        {shown.map((s) => {
          const g = siteAsGroup(s);
          const silent = g.fresh === false;
          return (
            <li key={s.id}>
              <Link href={`/hosts?site=${s.id}`} className={ROW}>
                <span className="min-w-0">
                  <span className="block truncate text-ui font-semibold text-fg">{s.name}</span>
                  <span className="block truncate text-meta text-fg-2">{s.host_count} hosts · {siteCheckers(s)}</span>
                </span>
                {silent ? (
                  <span aria-hidden="true" className={cn('ng-hatch block', BAR)} />
                ) : (
                  <StateBar byState={s.by_state} label={`${s.name}: ${describeStates(s.by_state)}`} className={BAR} />
                )}
                <span className={cn('whitespace-nowrap text-right text-meta', silent ? 'font-medium text-down' : 'text-fg-2')}>{groupSummary(g)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      {ranked.length > GROUP_CAP && (
        <button type="button" onClick={() => setAll((v) => !v)} className="mx-auto mt-2 text-meta font-medium text-accent hover:text-accent-hover">
          {all ? 'Show fewer' : `Show all ${ranked.length}`}
        </button>
      )}
      <StateLegend />
      <p className="mt-1.5 text-micro text-fg-3">
        {hidden > 0 && `${hidden} empty site${hidden === 1 ? '' : 's'} hidden. `}
        <Link href="/sites" className="text-accent hover:text-accent-hover">Manage sites</Link>
      </p>
    </DashboardCard>
  );
}

function HostsByChecker({ groups, loading, error, className }: {
  groups: HostGroup[] | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const { open } = useDashboardCtx();
  const [all, setAll] = useState(false);
  const ranked = rankGroups(groups ?? []);
  const shown = all ? ranked : ranked.slice(0, GROUP_CAP);
  const total = ranked.reduce((a, g) => a + g.host_count, 0);
  return (
    <DashboardCard id="dash-groups" title="Hosts by checker" meta={`${total} hosts · Direct or per probe`} loading={loading} error={error} className={className}>
      <ul className="flex flex-col gap-0.5">
        {shown.map((g) => {
          const silent = g.fresh === false && g.host_count > 0;
          return (
            <li key={`${g.kind}-${g.id ?? 'direct'}`}>
              <button
                type="button"
                onClick={() => open({ kind: 'group', group: g })}
                className={ROW}
              >
                <span className="min-w-0">
                  <span className="block truncate text-ui font-semibold text-fg">{g.name}</span>
                  <span className="block truncate text-meta text-fg-2">{g.host_count} hosts · {g.description}</span>
                </span>
                {silent ? (
                  <span aria-hidden="true" className={cn('ng-hatch block', BAR)} />
                ) : (
                  <StateBar byState={g.by_state} label={`${g.name}: ${describeStates(g.by_state)}`} className={BAR} />
                )}
                <span className={cn('whitespace-nowrap text-right text-meta', silent ? 'font-medium text-down' : 'text-fg-2')}>{groupSummary(g)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {ranked.length > GROUP_CAP && (
        <button type="button" onClick={() => setAll((v) => !v)} className="mx-auto mt-2 text-meta font-medium text-accent hover:text-accent-hover">
          {all ? 'Show fewer' : `Show all ${ranked.length}`}
        </button>
      )}
      <StateLegend />
    </DashboardCard>
  );
}
