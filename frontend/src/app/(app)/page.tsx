'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { QueryErrorState, StaleDataBanner } from '@/components/ui/QueryState';
import { FirstRunWelcome } from '@/components/dashboard/FirstRunWelcome';
import { DashboardProvider, type PanelTarget } from '@/components/dashboard/DashboardCard';
import { DashboardPanel } from '@/components/dashboard/DashboardPanel';
import { OpenIncidents } from '@/components/overview/OpenIncidents';
import { ComingUp, NeedsLook } from '@/components/overview/NeedsLook';
import { AtAGlance, HostsByGroup, OverviewHero } from '@/components/overview/AtAGlance';
import { useDashboardV2, useMarkDashboardSeen, useNavCounts } from '@/hooks/queries/useDashboard';
import { formatAgo, formatClock, isEmptyInstall, sectionError } from '@/lib/dashboard';
import { heroCounts, needItems } from '@/lib/overview';

function useNow(intervalMs = 15_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/**
 * Overview, concept F (docs/design/directions/concept-f.md): the state in one
 * sentence, open incidents first, everything else that needs a look in one
 * capped list, then figures and hosts by checker. Every problem appears in one
 * place; the page keeps its size from 50 to 500 hosts. The topology has its
 * own page.
 */
export default function DashboardPage() {
  useEffect(() => { document.title = 'Overview | Nodeglow'; }, []);
  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useDashboardV2();
  const { data: navCounts } = useNavCounts();
  const now = useNow();
  const [panel, setPanel] = useState<PanelTarget | null>(null);
  const open = useCallback((t: PanelTarget) => setPanel(t), []);
  const ctx = useMemo(() => ({ open, now }), [open, now]);

  useMarkDashboardSeen(!!data);

  const loading = isLoading && !data;
  const err = (name: string) => sectionError(data, name);
  const updated = data
    ? `${new Date(now).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}, ${formatClock(new Date(now).toISOString())} · Updated ${formatAgo(new Date(dataUpdatedAt).toISOString(), now)}`
    : 'Current state of your infrastructure';
  const needs = useMemo(() => needItems(data, navCounts, now), [data, navCounts, now]);

  if (!data && isError) {
    return (
      <div>
        <PageHeader title="Overview" description={updated} />
        <Card>
          <QueryErrorState error={error} onRetry={refetch} title="Could not load the overview" />
        </Card>
      </div>
    );
  }

  if (isEmptyInstall(data)) {
    return (
      <div>
        <PageHeader title="Overview" description={updated} />
        <FirstRunWelcome />
      </div>
    );
  }

  const openCount = data?.incidents?.counts.open ?? data?.summary?.incidents.open ?? null;

  return (
    <DashboardProvider value={ctx}>
      <OverviewHero
        open={loading ? null : openCount}
        counts={data?.health ? heroCounts(data.health) : null}
        health={data?.health}
        slv={data?.since_last_visit}
        updated={updated}
      />
      {isError && data && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}

      {/* Two columns from 1180 px; below, one column in the order incidents, needs a look, coming up, figures, hosts. */}
      <div className="grid grid-cols-[minmax(0,1fr)_400px] items-start gap-4 max-[1179px]:flex max-[1179px]:flex-col max-[1179px]:items-stretch">
        <div className="flex min-w-0 flex-col gap-4 max-[1179px]:contents">
          <OpenIncidents
            incidents={data?.incidents}
            topology={data?.topology}
            latency={data?.latency}
            loading={loading}
            error={err('incidents')}
            generatedAt={data?.generated_at}
            className="max-[1179px]:order-1"
          />
          <AtAGlance
            incidents={data?.incidents}
            syslog={data?.syslog}
            availability={data?.availability}
            health={data?.health}
            loading={loading}
            error={sectionError(data, 'syslog', 'availability')}
            className="max-[1179px]:order-4"
          />
          <HostsByGroup groups={data?.groups} sites={data?.sites} loading={loading} error={err('groups') ?? err('sites')} className="max-[1179px]:order-5" />
        </div>
        <div className="flex min-w-0 flex-col gap-4 max-[1179px]:contents">
          <NeedsLook items={needs} loading={loading} error={sectionError(data, 'health', 'groups', 'upcoming')} generatedAt={data?.generated_at} className="max-[1179px]:order-2" />
          <ComingUp upcoming={data?.upcoming} loading={loading} error={err('upcoming')} className="max-[1179px]:order-3" />
        </div>
      </div>

      <DashboardPanel target={panel} data={data} onClose={() => setPanel(null)} />
    </DashboardProvider>
  );
}
