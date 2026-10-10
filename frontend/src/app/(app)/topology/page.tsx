'use client';

import { useEffect, useMemo, useState } from 'react';
import { ListTree, Map as MapIcon, Network, SearchX } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Field';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { TopologyMap } from '@/components/topology/TopologyMap';
import { TopologyTree } from '@/components/topology/TopologyTree';
import { applyFilter, buildTrees, matcher, nodeState, type TopoFilter } from '@/components/topology/model';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useTopology } from '@/hooks/queries/useTopology';

type View = 'tree' | 'map';

function writeUrl(view: View, filter: TopoFilter) {
  const url = new URL(window.location.href);
  if (view === 'map') url.searchParams.set('view', 'map'); else url.searchParams.delete('view');
  if (filter === 'problems') url.searchParams.set('show', 'problems'); else url.searchParams.delete('show');
  window.history.replaceState(window.history.state, '', url);
}

/** Legend in the E3 wording: no light = no data. */
function Legend() {
  return (
    <ul className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border pt-3 text-meta text-fg-3" aria-label="Legend">
      <li className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="w-[18px] border-t-2 border-line" />Uplink</li>
      <li className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="w-[18px] border-t-2 border-down [filter:var(--ng-wire-glow)]" />Affected branch · glows</li>
      <li className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="w-[18px] border-t-2 border-dashed border-unknown" />No data · no light = no data</li>
      <li className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="h-2 w-2 rounded-full bg-maint" />Maintenance</li>
      <li className="ml-auto max-sm:ml-0">Links from parent assignments (manual, UniFi, Proxmox)</li>
    </ul>
  );
}

export default function TopologyPage() {
  useEffect(() => { document.title = 'Topology | Nodeglow'; }, []);
  const query = useTopology();
  const { data } = query;
  const [view, setView] = useState<View>('tree');
  const [filter, setFilter] = useState<TopoFilter>('all');
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search, 200);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get('view') === 'map') setView('map');
    if (q.get('show') === 'problems') setFilter('problems');
  }, []);
  const changeView = (v: View) => { setView(v); writeUrl(v, filter); };
  const changeFilter = (f: TopoFilter) => { setFilter(f); writeUrl(view, f); };

  const all = useMemo(() => (data ? buildTrees(data.nodes, data.edges) : { trees: [], orphans: [] }), [data]);
  const shown = useMemo(() => applyFilter(all.trees, all.orphans, matcher(filter, debounced)), [all, filter, debounced]);

  const counts = useMemo(() => {
    const c = { ok: 0, down: 0, unknown: 0, maint: 0 };
    for (const n of data?.nodes ?? []) {
      const s = nodeState(n);
      if (s === 'ok' || s === 'down' || s === 'unknown' || s === 'maint') c[s] += 1;
    }
    return c;
  }, [data]);

  const resetFilters = () => { setSearch(''); changeFilter('all'); };
  const nothingShown = shown.trees.length === 0 && shown.orphans.length === 0;

  return (
    <div>
      <PageHeader
        title="Topology"
        description={data ? `Dependencies from parent assignments · updated ${formatAsOf(query.dataUpdatedAt)}` : 'Dependencies from parent assignments'}
      />

      <QueryState
        query={query}
        errorTitle="Could not load the topology"
        isEmpty={(d) => d.nodes.length === 0}
        loading={
          <div className="space-y-4" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-[92px] w-full rounded-card" />
            <Skeleton className="h-[520px] w-full rounded-card" />
          </div>
        }
        empty={
          <Card>
            <EmptyState
              icon={Network}
              title="No topology yet"
              description="Add hosts and set a parent (or connect UniFi / Proxmox) to build the dependency map."
            />
          </Card>
        }
      >
        {(d) => (
          <div className="space-y-4">
            <Card>
              <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-6">
                <BigNumber size="sm" value={d.nodes.length} label="Nodes" />
                <BigNumber size="sm" value={counts.ok} label="OK" />
                <BigNumber size="sm" value={counts.down} state={counts.down > 0 ? 'down' : undefined} label="Down" />
                <BigNumber size="sm" value={counts.unknown} state={counts.unknown > 0 ? 'unknown' : undefined} label="No data" />
                <BigNumber size="sm" value={counts.maint} state={counts.maint > 0 ? 'maint' : undefined} label="Maintenance" />
                <BigNumber size="sm" value={d.edges.length} label="Links" />
              </div>
            </Card>

            <Card as="section" aria-label="Topology">
              <div className="mb-5 flex flex-wrap items-center gap-3">
                <SegmentedControl<View>
                  label="View"
                  value={view}
                  onChange={changeView}
                  options={[
                    { value: 'tree', label: <><ListTree size={14} aria-hidden="true" />Tree</> },
                    { value: 'map', label: <><MapIcon size={14} aria-hidden="true" />Map</> },
                  ]}
                />
                <SegmentedControl<TopoFilter>
                  label="Show"
                  value={filter}
                  onChange={changeFilter}
                  options={[
                    { value: 'all', label: 'All' },
                    { value: 'problems', label: `Problems · ${counts.down + counts.unknown}` },
                  ]}
                />
                <Input
                  type="search"
                  aria-label="Filter by name or hostname"
                  placeholder="Filter by name or hostname"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full sm:ml-auto sm:w-64"
                />
              </div>

              {nothingShown ? (
                <EmptyState
                  variant="no-results"
                  icon={SearchX}
                  title={filter === 'problems' && !debounced.trim() ? 'No down or unobserved hosts' : 'No hosts match the filter'}
                  description={filter === 'problems' && !debounced.trim() ? 'Every node has a fresh result or is in maintenance.' : undefined}
                  action={<Button variant="secondary" size="sm" onClick={resetFilters}>Show all</Button>}
                />
              ) : view === 'tree' ? (
                <TopologyTree trees={shown.trees} orphans={shown.orphans} />
              ) : (
                <div className="-mx-[20px] border-y border-border max-[759px]:-mx-[16px]">
                  <TopologyMap trees={shown.trees} orphans={shown.orphans} />
                </div>
              )}
              <Legend />
            </Card>
          </div>
        )}
      </QueryState>
    </div>
  );
}
