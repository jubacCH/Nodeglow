'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, Search, Server, X } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button, IconButton } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { ExportButton } from '@/components/ui/ExportButton';
import { Checkbox, Input } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAgents } from '@/hooks/queries/useAgents';
import { useHostsV1, useHostsV1ByState, type HostListItem } from '@/hooks/queries/useHosts';
import { cn } from '@/lib/utils';
import { BulkActionsBar } from '@/components/hosts/BulkActionsBar';
import type { BulkReport } from '@/components/hosts/bulk';
import { HostCards, HostTable, toRow, type HostRow, type SortDir, type SortKey } from '@/components/hosts/HostList';
import { HostFormModal } from '@/components/hosts/HostFormModal';
import { StateFilterChips } from '@/components/hosts/StateFilterChips';
import {
  HOST_STATES, hostStateRank, normalizeHostState, stateFromLegacyParam, type HostState,
} from '@/components/hosts/hostState';
import type { ProbeAgent } from '@/components/hosts/probes';

const PAGE_SIZE = 50;

function compare(a: HostRow, b: HostRow, key: SortKey): number {
  switch (key) {
    case 'state': return hostStateRank(a.st) - hostStateRank(b.st) || a.name.localeCompare(b.name);
    case 'name': return a.name.localeCompare(b.name);
    case 'latency': return (a.latency_ms ?? Infinity) - (b.latency_ms ?? Infinity);
    case 'uptime': return (a.uptime?.h24 ?? Infinity) - (b.uptime?.h24 ?? Infinity);
    case 'observed': return (b.observed_at ? Date.parse(b.observed_at) : 0) - (a.observed_at ? Date.parse(a.observed_at) : 0);
  }
}

function matches(h: HostListItem, q: string): boolean {
  return (
    h.name.toLowerCase().includes(q) ||
    h.hostname.toLowerCase().includes(q) ||
    (h.check_type ?? '').toLowerCase().includes(q) ||
    (h.source ?? '').toLowerCase().includes(q) ||
    (h.state_reason ?? '').toLowerCase().includes(q)
  );
}

function HostsPageInner() {
  useEffect(() => { document.title = 'Hosts | Nodeglow'; }, []);
  const searchParams = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const qParam = searchParams.get('q') ?? '';
  const [search, setSearch] = useState(qParam);
  const [stateFilter, setStateFilter] = useState<HostState | null>(
    () => stateFromLegacyParam(searchParams.get('state')) ?? stateFromLegacyParam(searchParams.get('status')),
  );
  const [sortKey, setSortKey] = useState<SortKey>('state');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [report, setReport] = useState<BulkReport | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const redirected = useRef(false);

  // All hosts (for the chip counts) and the server-filtered list.
  const all = useHostsV1();
  const listQuery = useHostsV1ByState(stateFilter ? [stateFilter] : []);
  const { data: agents } = useAgents();

  const probeNames = useMemo(() => {
    const m = new Map<number, { name: string; silent: boolean }>();
    for (const a of (agents ?? []) as ProbeAgent[]) if (a.is_probe) m.set(a.id, { name: a.name, silent: !!a.probe?.stale });
    return m;
  }, [agents]);

  const counts = useMemo(() => {
    if (!all.data) return undefined;
    const c = Object.fromEntries(HOST_STATES.map((s) => [s, 0])) as Record<HostState, number>;
    for (const h of all.data) c[normalizeHostState(h.state)] += 1;
    return c;
  }, [all.data]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = (listQuery.data ?? []).filter((h) => !q || matches(h, q)).map((h) => toRow(h, probeNames));
    const mult = sortDir === 'asc' ? 1 : -1;
    return list.sort((a, b) => mult * compare(a, b, sortKey));
  }, [listQuery.data, search, sortKey, sortDir, probeNames]);

  useEffect(() => { setPage(0); }, [search, stateFilter, sortKey, sortDir]);
  const paged = useMemo(() => rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), [rows, page]);

  // Keep the filter in the URL so it can be shared and survives reloads.
  const changeFilter = useCallback((s: HostState | null) => {
    setStateFilter(s);
    const p = new URLSearchParams(searchParams.toString());
    p.delete('status');
    if (s) p.set('state', s); else p.delete('state');
    const qs = p.toString();
    router.replace(qs ? `/hosts?${qs}` : '/hosts', { scroll: false });
  }, [router, searchParams]);

  // Deep link "?q=…" with exactly one match opens that host.
  useEffect(() => {
    if (qParam && !listQuery.isLoading && rows.length === 1 && !redirected.current) {
      redirected.current = true;
      router.replace(`/hosts/${rows[0].id}`);
    }
  }, [qParam, listQuery.isLoading, rows, router]);

  // Drop selected hosts that are no longer listed (filter change, deletion).
  const visibleIds = useMemo(() => new Set(rows.map((r) => r.id)), [rows]);
  const selectedIds = useMemo(() => [...selected].filter((id) => visibleIds.has(id)), [selected, visibleIds]);
  const names = useMemo(() => new Map((all.data ?? []).map((h) => [h.id, h.name])), [all.data]);

  const toggle = (id: number) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const allSelected = rows.length > 0 && selectedIds.length === rows.length;
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)));
  const onSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('asc'); }
  };

  const asOf = formatAsOf(listQuery.dataUpdatedAt);
  const total = all.data?.length;
  const filtered = !!stateFilter || !!search.trim();

  return (
    <div>
      <PageHeader
        title="Hosts"
        description={
          total === undefined
            ? 'Every monitored host and its current state'
            : `${total} host${total === 1 ? '' : 's'}${asOf ? ` · updated ${asOf}` : ''}`
        }
        actions={
          <>
            <ExportButton
              data={rows.map((h) => ({
                name: h.name, hostname: h.hostname, state: h.st, reason: h.state_reason ?? '',
                observed_at: h.observed_at ?? '', check_type: h.check_type, checked_by: h.checkedBy,
                source: h.source, latency_ms: h.latency_ms,
                uptime_24h: h.uptime?.h24, uptime_7d: h.uptime?.d7, uptime_30d: h.uptime?.d30,
              }))}
              filename="hosts"
              columns={[
                { key: 'name', label: 'Name' }, { key: 'hostname', label: 'Hostname' },
                { key: 'state', label: 'State' }, { key: 'reason', label: 'Reason' },
                { key: 'observed_at', label: 'Observed at (UTC)' }, { key: 'check_type', label: 'Checks' },
                { key: 'checked_by', label: 'Checked by' }, { key: 'source', label: 'Source' },
                { key: 'latency_ms', label: 'Latency (ms)' }, { key: 'uptime_24h', label: 'Uptime 24h' },
                { key: 'uptime_7d', label: 'Uptime 7d' }, { key: 'uptime_30d', label: 'Uptime 30d' },
              ]}
            />
            <Button onClick={() => setShowAdd(true)}>
              <Plus size={15} aria-hidden="true" /> Add host
            </Button>
          </>
        }
      />

      <div className="mb-4 flex flex-col gap-3">
        <div className="relative w-full min-[760px]:max-w-[360px]">
          <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-3" />
          <Input
            type="search"
            aria-label="Search hosts"
            placeholder="Search name, address, reason…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 pr-9"
          />
          {search && (
            <IconButton aria-label="Clear search" size="sm" className="absolute right-1 top-1/2 -translate-y-1/2" onClick={() => setSearch('')}>
              <X size={14} aria-hidden="true" />
            </IconButton>
          )}
        </div>
        <StateFilterChips value={stateFilter} onChange={changeFilter} counts={counts} total={total} />
      </div>

      {selectedIds.length > 0 && (
        <BulkActionsBar
          ids={selectedIds}
          names={names}
          onClear={() => setSelected(new Set())}
          onReport={setReport}
        />
      )}

      {report && report.details.length > 0 && (
        <div
          role="status"
          className={cn(
            'mb-3 flex items-start gap-2 rounded-ctl border px-3 py-2 text-meta',
            report.tone === 'error' ? 'border-down/30 bg-down-soft text-down' : 'border-degraded/40 bg-degraded-soft text-degraded',
          )}
        >
          <div className="min-w-0 flex-1">
            <p className="font-medium">{report.message}</p>
            {report.details.map((d) => <p key={d}>{d}</p>)}
          </div>
          <IconButton aria-label="Dismiss" size="sm" className="-my-1 text-current" onClick={() => setReport(null)}>
            <X size={14} aria-hidden="true" />
          </IconButton>
        </div>
      )}

      <Card padding="none">
        <QueryState
          query={listQuery}
          errorTitle="Could not load hosts"
          loading={
            <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading hosts">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
            </div>
          }
        >
          {(data) =>
            data.length === 0 && !filtered ? (
              <EmptyState
                icon={Server}
                title="No hosts yet"
                description="Add a host, or connect an integration or agent to discover them."
                action={<Button onClick={() => setShowAdd(true)}><Plus size={15} aria-hidden="true" /> Add host</Button>}
              />
            ) : rows.length === 0 ? (
              <EmptyState
                variant="no-results"
                title={stateFilter ? 'No hosts in this state' : 'No hosts match your search'}
                description={search ? `Nothing matches “${search}”.` : 'Pick another state or show all hosts.'}
                action={
                  <Button variant="secondary" onClick={() => { setSearch(''); changeFilter(null); }}>
                    Reset filters
                  </Button>
                }
              />
            ) : (
              <>
                <div className="max-[759px]:hidden">
                  <HostTable
                    rows={paged}
                    selected={selected}
                    onToggle={toggle}
                    onToggleAll={toggleAll}
                    allSelected={allSelected}
                    someSelected={selectedIds.length > 0}
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={onSort}
                    busy={listQuery.isPlaceholderData}
                  />
                </div>
                <div className="min-[760px]:hidden">
                  <div className="flex items-center justify-between border-b border-border px-4 py-2">
                    <Checkbox label={`Select all ${rows.length}`} checked={allSelected} onChange={toggleAll} />
                    {sortKey === 'state' && sortDir === 'asc' && <span className="text-meta text-fg-3">Worst first</span>}
                  </div>
                  <HostCards rows={paged} selected={selected} onToggle={toggle} busy={listQuery.isPlaceholderData} />
                </div>
                <Pagination page={page} pageSize={PAGE_SIZE} total={rows.length} onPageChange={setPage} />
              </>
            )
          }
        </QueryState>
      </Card>

      <HostFormModal
        open={showAdd}
        mode="add"
        onClose={() => setShowAdd(false)}
        onSaved={(id) => {
          qc.invalidateQueries({ queryKey: ['hosts-v1'] });
          qc.invalidateQueries({ queryKey: ['hosts'] });
          if (id) router.push(`/hosts/${id}`);
        }}
      />
    </div>
  );
}

export default function HostsPage() {
  return (
    <Suspense>
      <HostsPageInner />
    </Suspense>
  );
}
