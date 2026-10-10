'use client';

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Bell, ShieldCheck } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Pagination } from '@/components/ui/Pagination';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { IncidentFilters } from '@/components/incidents/IncidentFilters';
import { IncidentTable } from '@/components/incidents/IncidentTable';
import { MaintenanceHostsCard } from '@/components/maintenance/MaintenanceHostsCard';
import { MaintenanceWindowsPanel } from '@/components/maintenance/MaintenanceWindowsPanel';
import { useIncidentAction, useIncidentList } from '@/hooks/queries/useAlerts';
import {
  DEFAULT_STATUSES, KNOWN_RULES, PAGE_SIZE, filtersFromParams, filtersToParams, hasActiveFilters, incidentQueryString,
  type IncidentFilters as Filters, type IncidentItem, type IncidentPage, type IncidentSort,
} from '@/lib/incidents';
import { useIsEditor } from '@/stores/auth';

/** Re-render once a minute so ages stay current between refetches. */
function useNow(intervalMs = 60_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function ListSkeleton() {
  return (
    <Card padding="none" aria-busy="true" aria-label="Loading incidents">
      <div className="divide-y divide-border">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-4 w-24 max-[759px]:hidden" />
            <Skeleton className="h-4 w-16" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function IncidentsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => filtersFromParams(new URLSearchParams(params.toString())), [params]);
  const now = useNow();
  // The `from` boundary moves with the minute clock, so a time-range filter
  // follows "last 24 h" without changing the query key on every render.
  const qs = useMemo(() => incidentQueryString(filters, now), [filters, now]);
  const query = useIncidentList(qs);
  const action = useIncidentAction();
  const canEdit = useIsEditor();

  const setFilters = useCallback((next: Filters) => {
    const p = filtersToParams(next).toString();
    router.replace(p ? `${pathname}?${p}` : pathname, { scroll: false });
  }, [router, pathname]);

  const reset = () => setFilters({ ...filters, statuses: [...DEFAULT_STATUSES], severities: [], rule: '', range: 'all', search: '', page: 0 });

  const rules = useMemo(() => {
    const seen = new Set(KNOWN_RULES);
    query.data?.items.forEach((i) => seen.add(i.rule));
    if (filters.rule) seen.add(filters.rule);
    return Array.from(seen).sort();
  }, [query.data, filters.rule]);

  const total = query.data?.total;
  const onlyOpen = filters.statuses.every((s) => s !== 'resolved');
  const asOf = formatAsOf(query.dataUpdatedAt);
  const filtered = hasActiveFilters(filters);

  // Last page emptied (e.g. after resolving its only row): step back.
  useEffect(() => {
    const d = query.data;
    if (d && d.items.length === 0 && d.total > 0 && filters.page > 0) {
      setFilters({ ...filters, page: Math.max(0, Math.ceil(d.total / PAGE_SIZE) - 1) });
    }
  }, [query.data, filters, setFilters]);

  return (
    <>
      <PageHeader
        title="Incidents"
        description={
          total === undefined
            ? 'Correlated incidents from rules and checks'
            : `${total} ${onlyOpen ? 'open' : 'matching'} incident${total === 1 ? '' : 's'}${asOf ? ` · updated ${asOf}` : ''}`
        }
      />
      <IncidentFilters value={filters} onChange={setFilters} rules={rules} onReset={reset} />
      <QueryState<IncidentPage>
        query={query}
        errorTitle="Could not load incidents"
        loading={<ListSkeleton />}
        isEmpty={(d) => d.total === 0}
        empty={
          <Card>
            {filtered ? (
              <EmptyState
                variant="no-results"
                icon={Bell}
                title="No incidents match these filters"
                description="Widen the time range or include other statuses."
                action={<Button variant="secondary" size="sm" onClick={reset}>Reset filters</Button>}
              />
            ) : (
              <EmptyState
                variant="confirmed"
                icon={ShieldCheck}
                title="No open incidents"
                description="Nothing is open or waiting for acknowledgement."
                asOf={asOf}
              />
            )}
          </Card>
        }
      >
        {(page) => (
          <div aria-busy={query.isFetching && query.isPlaceholderData ? true : undefined} className={query.isPlaceholderData ? 'opacity-70 transition-opacity' : undefined}>
            <IncidentTable
              items={page.items}
              sort={filters.sort}
              onSort={(s: IncidentSort) => setFilters({ ...filters, sort: s, page: 0 })}
              now={now}
              actions={{
                canEdit,
                pendingId: action.isPending ? action.variables?.id ?? null : null,
                onAcknowledge: (inc: IncidentItem) => action.mutate({ id: inc.id, action: 'acknowledge' }),
                onResolve: (inc: IncidentItem) => action.mutate({ id: inc.id, action: 'resolve' }),
              }}
            />
            <nav aria-label="Incident pages" className="mt-2 [&>div]:border-t-0 [&>div]:px-1">
              <Pagination page={filters.page} pageSize={PAGE_SIZE} total={page.total} onPageChange={(p) => setFilters({ ...filters, page: p })} />
            </nav>
          </div>
        )}
      </QueryState>
    </>
  );
}

function MaintenanceView() {
  return (
    <>
      <PageHeader title="Maintenance" description="Scheduled windows and hosts currently in maintenance. Hosts in maintenance are not alerted." />
      <MaintenanceWindowsPanel />
      <MaintenanceHostsCard />
    </>
  );
}

function AlertsPageInner() {
  const tab = useSearchParams().get('tab');
  const maintenance = tab === 'maintenance';
  useEffect(() => {
    document.title = `${maintenance ? 'Maintenance' : 'Incidents'} | Nodeglow`;
  }, [maintenance]);
  // Section navigation (Incidents / Maintenance / Alert rules) lives in the
  // shell sub-nav; this page only renders the view the URL asks for.
  return maintenance ? <MaintenanceView /> : <IncidentsView />;
}

export default function AlertsPage() {
  return (
    <Suspense>
      <AlertsPageInner />
    </Suspense>
  );
}
