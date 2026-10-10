'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { QueryErrorState, StaleDataBanner } from '@/components/ui/QueryState';
import { FirstRunWelcome } from '@/components/dashboard/FirstRunWelcome';
import { DashboardProvider, type PanelTarget } from '@/components/dashboard/DashboardCard';
import { DashboardPanel } from '@/components/dashboard/DashboardPanel';
import { HealthCard } from '@/components/dashboard/HealthCard';
import { InternetCard } from '@/components/dashboard/InternetCard';
import { IncidentsCard } from '@/components/dashboard/IncidentsCard';
import { TopologyCard } from '@/components/dashboard/TopologyCard';
import { GroupsCard } from '@/components/dashboard/GroupsCard';
import { UpcomingCard } from '@/components/dashboard/UpcomingCard';
import { LatencyCard } from '@/components/dashboard/LatencyCard';
import { SyslogCard } from '@/components/dashboard/SyslogCard';
import { AvailabilityCard } from '@/components/dashboard/AvailabilityCard';
import { SinceLastVisitCard } from '@/components/dashboard/SinceLastVisitCard';
import { useDashboardV2, useMarkDashboardSeen } from '@/hooks/queries/useDashboard';
import { formatAgo, formatClock, formatWhen, isEmptyInstall, sectionError } from '@/lib/dashboard';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';

/** Card placement (E3 grid): 12 columns ≥ 1200 px, 2 below, 1 on phones. */
const AREA = {
  health: 'col-span-5 max-[1199px]:col-span-2 max-[759px]:col-span-1 max-[759px]:order-1',
  inet: 'col-span-3 max-[1199px]:col-span-1 max-[759px]:order-3',
  inc: 'col-span-4 max-[1199px]:col-span-1 max-[759px]:order-2',
  topo: 'col-span-8 row-span-2 max-[1439px]:col-span-12 max-[1439px]:row-span-1 max-[1199px]:col-span-2 max-[759px]:col-span-1 max-[759px]:order-4',
  groups: 'col-span-4 max-[1439px]:col-span-6 max-[1199px]:col-span-1 max-[759px]:order-5',
  upc: 'col-span-4 max-[1439px]:col-span-6 max-[1199px]:col-span-1 max-[759px]:order-6',
  lat: 'col-span-6 max-[1199px]:col-span-2 max-[759px]:col-span-1 max-[759px]:order-7',
  sys: 'col-span-3 max-[1199px]:col-span-1 max-[759px]:order-8',
  avail: 'col-span-3 max-[1199px]:col-span-1 max-[759px]:order-9',
  chg: 'col-span-12 max-[1199px]:col-span-2 max-[759px]:col-span-1 max-[759px]:order-10',
} as const;

function useNow(intervalMs = 15_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export default function DashboardPage() {
  useEffect(() => { document.title = 'Overview | Nodeglow'; }, []);
  const q = useDashboardV2();
  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = q;
  const now = useNow();
  const reduced = usePrefersReducedMotion();
  const [panel, setPanel] = useState<PanelTarget | null>(null);
  const open = useCallback((t: PanelTarget) => setPanel(t), []);
  const ctx = useMemo(() => ({ open, now }), [open, now]);

  useMarkDashboardSeen(!!data);

  const loading = isLoading && !data;
  const err = (name: string) => sectionError(data, name);
  const slv = data?.since_last_visit;
  const opened = slv?.counts.incident_opened ?? 0;
  const resolved = slv?.counts.incident_resolved ?? 0;

  const goToChanges = () => {
    const el = document.getElementById('dash-since');
    if (!el) return;
    el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
    el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true });
  };

  const header = (
    <PageHeader
      title="Overview"
      description={
        data
          ? `${new Date(now).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}, ${formatClock(new Date(now).toISOString())} · Updated ${formatAgo(new Date(dataUpdatedAt).toISOString(), now)}`
          : 'Current state of your infrastructure'
      }
      actions={
        slv && (
          <button
            type="button"
            onClick={goToChanges}
            className="inline-flex h-[34px] items-center gap-2 rounded-ctl border border-border bg-surface px-3 text-ui text-fg-2 hover:border-border-2 hover:text-fg"
          >
            <span>
              {slv.fallback ? 'Last 24 h' : <>Since your last visit <b className="font-medium text-fg">{formatWhen(slv.since, now)}</b></>}
            </span>
            <span className="text-fg-3" aria-hidden="true">·</span>
            <span><b className="num font-medium text-fg">{opened}</b> opened · <b className="num font-medium text-fg">{resolved}</b> resolved</span>
          </button>
        )
      }
    />
  );

  if (!data && isError) {
    return (
      <div>
        {header}
        <Card>
          <QueryErrorState error={error} onRetry={refetch} title="Could not load the overview" />
        </Card>
      </div>
    );
  }

  if (isEmptyInstall(data)) {
    return (
      <div>
        {header}
        <FirstRunWelcome />
      </div>
    );
  }

  const items = data?.incidents?.items ?? [];

  return (
    <DashboardProvider value={ctx}>
      {header}
      {isError && data && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}

      <div className="grid grid-cols-12 gap-4 max-[1199px]:grid-cols-2 max-[759px]:grid-cols-1">
        <HealthCard
          className={AREA.health}
          health={data?.health}
          incidents={data?.incidents?.counts ?? data?.summary?.incidents}
          loading={loading}
          error={err('health')}
          countsError={sectionError(data, '_integrations', '_agents')}
        />
        <InternetCard className={AREA.inet} internet={data?.internet} loading={loading} error={err('internet')} />
        <IncidentsCard className={AREA.inc} incidents={data?.incidents} loading={loading} error={err('incidents')} generatedAt={data?.generated_at} />
        <TopologyCard
          className={AREA.topo}
          topology={data?.topology}
          groups={data?.groups}
          internet={data?.internet}
          incidents={items}
          loading={loading}
          error={err('topology')}
        />
        <GroupsCard className={AREA.groups} groups={data?.groups} loading={loading} error={err('groups')} />
        <UpcomingCard className={AREA.upc} upcoming={data?.upcoming} loading={loading} error={err('upcoming')} generatedAt={data?.generated_at} />
        <LatencyCard className={AREA.lat} latency={data?.latency} loading={loading} error={err('latency')} />
        <SyslogCard className={AREA.sys} syslog={data?.syslog} loading={loading} error={err('syslog')} />
        <AvailabilityCard className={AREA.avail} availability={data?.availability} loading={loading} error={err('availability')} />
        <SinceLastVisitCard className={AREA.chg} slv={slv} loading={loading} error={err('since_last_visit')} generatedAt={data?.generated_at} />
      </div>

      <DashboardPanel target={panel} data={data} onClose={() => setPanel(null)} />
    </DashboardProvider>
  );
}
