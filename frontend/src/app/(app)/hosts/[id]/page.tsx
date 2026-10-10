'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { FileText, Pencil, RefreshCw } from 'lucide-react';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button, IconButton, buttonClasses } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { CopyButton } from '@/components/ui/CopyButton';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { TabPanel, Tabs } from '@/components/ui/Tabs';
import { useAgents } from '@/hooks/queries/useAgents';
import { useHost } from '@/hooks/queries/useHosts';
import { ApiError } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { HostStatePill } from '@/components/hosts/HostStatePill';
import { HostFormModal } from '@/components/hosts/HostFormModal';
import { HostTimeline } from '@/components/hosts/HostTimeline';
import { normalizeHostState } from '@/components/hosts/hostState';
import { probeById, type ProbeAgent } from '@/components/hosts/probes';
import { ChecksCard } from '@/components/hosts/detail/ChecksCard';
import { HostIncidentsCard } from '@/components/hosts/detail/HostIncidentsCard';
import { HostStateBanner } from '@/components/hosts/detail/HostStateBanner';
import { LatencyCard } from '@/components/hosts/detail/LatencyCard';
import { MaintenanceCard } from '@/components/hosts/detail/MaintenanceCard';
import { AgentSection, DeviceSection, HostFactsCard } from '@/components/hosts/detail/MetricSections';
import { DiscoveredPortsCard, HostSyslog, PortSummaryCard, PortsTab } from '@/components/hosts/detail/PortSections';
import { ProbeCard } from '@/components/hosts/detail/ProbeCard';
import { UptimeTiles } from '@/components/hosts/detail/UptimeTiles';
import type { HostDetailData, PortInfo } from '@/components/hosts/detail/types';

type Tab = 'overview' | 'ports' | 'timeline' | 'syslog';
const TAB_BASE = 'host-tabs';

export default function HostDetailPage() {
  const params = useParams();
  const hostId = Number(params.id);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const [tab, setTab] = useState<Tab>('overview');
  const [showEdit, setShowEdit] = useState(false);
  const hostQuery = useHost(hostId);
  const host = hostQuery.data as unknown as HostDetailData | undefined;
  const { data: agents } = useAgents();
  const probes = ((agents ?? []) as ProbeAgent[]).filter((a) => a.is_probe);

  useEffect(() => {
    document.title = `${host?.name ?? 'Host'} | Nodeglow`;
  }, [host?.name]);

  const state = normalizeHostState(host?.state);
  const probe = probeById(probes, host?.probe_id);
  const checkedBy = host?.probe_id == null ? 'Core (direct)' : probe?.name ?? `Probe #${host.probe_id}`;
  const device = host?.integration?.device ?? null;
  const portTable: PortInfo[] = Array.isArray(device?.port_table) ? device.port_table : [];
  const hasPorts = !!device?.has_ports && portTable.length > 0;
  const stale = state === 'unknown' || state === 'disabled';

  const crumbs = [{ label: 'Hosts', href: '/hosts' }, { label: host?.name ?? `Host #${hostId}` }];

  // A failed or 404 request must not render an empty host page.
  if (!hostQuery.isLoading && !host && hostQuery.isError) {
    const notFound = hostQuery.error instanceof ApiError && hostQuery.error.status === 404;
    return (
      <div>
        <Breadcrumbs items={crumbs} />
        <Card className="mt-4">
          <QueryErrorState
            error={hostQuery.error}
            onRetry={notFound ? undefined : hostQuery.refetch}
            title={notFound ? 'Host not found' : 'Could not load this host'}
          />
          {notFound && (
            <div className="-mt-6 pb-6 text-center">
              <Link href="/hosts" className={buttonClasses({ variant: 'secondary', size: 'sm' })}>Back to hosts</Link>
            </div>
          )}
        </Card>
      </div>
    );
  }

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['host', hostId] });
    qc.invalidateQueries({ queryKey: ['host-history', hostId] });
    qc.invalidateQueries({ queryKey: ['host-incidents', hostId] });
  };
  const asOf = formatAsOf(hostQuery.dataUpdatedAt);

  const tabs = [
    { id: 'overview' as const, label: 'Overview' },
    ...(hasPorts ? [{ id: 'ports' as const, label: 'Ports', count: portTable.length }] : []),
    { id: 'timeline' as const, label: 'Timeline' },
    { id: 'syslog' as const, label: 'Syslog' },
  ];

  return (
    <div>
      <Breadcrumbs items={crumbs} />
      {hostQuery.isError && host && (
        <StaleDataBanner error={hostQuery.error} onRetry={hostQuery.refetch} updatedAt={hostQuery.dataUpdatedAt} />
      )}
      <PageHeader
        title={host?.name ?? (hostQuery.isLoading ? 'Loading host…' : 'Host')}
        status={host ? <HostStatePill state={state} reason={host.state_reason} observedAt={host.observed_at} /> : undefined}
        description={
          host ? (
            <span className="inline-flex flex-wrap items-center gap-x-1.5">
              <span className="font-mono">{host.hostname}</span>
              <CopyButton text={host.hostname} size={12} />
              <span className="text-fg-3">· {host.source}{asOf ? ` · updated ${asOf}` : ''}</span>
            </span>
          ) : undefined
        }
        actions={
          host && (
            <>
              {host.agent && (
                <Link href={`/agents/${host.agent.agent_id}`} className={buttonClasses({ variant: 'secondary' })}>
                  <FileText size={14} aria-hidden="true" /> Agent &amp; logs
                </Link>
              )}
              <Button variant="secondary" onClick={() => setShowEdit(true)}>
                <Pencil size={14} aria-hidden="true" /> Edit
              </Button>
              <IconButton aria-label="Refresh" variant="secondary" onClick={refresh} disabled={hostQuery.isFetching}>
                <RefreshCw size={15} aria-hidden="true" className={hostQuery.isFetching ? 'animate-spin' : undefined} />
              </IconButton>
            </>
          )
        }
      />

      {hostQuery.isLoading || !host ? (
        <div className="space-y-4" aria-busy="true" aria-label="Loading host">
          <Card><Skeleton className="mb-2 h-7 w-40" /><Skeleton className="h-5 w-72" /></Card>
          <UptimeTiles state="unknown" loading />
          <Card><Skeleton className="h-[220px] w-full" /></Card>
        </div>
      ) : (
        <>
          <HostStateBanner host={host} state={state} probe={probe} />

          <Tabs label="Host sections" idBase={TAB_BASE} items={tabs} value={tab} onChange={setTab} className="mb-5" />

          <TabPanel idBase={TAB_BASE} id="overview" active={tab === 'overview'} className="space-y-4">
            <UptimeTiles host={host} state={state} />
            <div className="grid grid-cols-12 gap-4">
              <LatencyCard hostId={hostId} threshold={host.latency_threshold_ms} className="col-span-8 max-[1199px]:col-span-12" />
              <HostFactsCard host={host} checkedBy={checkedBy} className="col-span-4 max-[1199px]:col-span-12" />
              <ChecksCard host={host} state={state} className="col-span-6 max-[999px]:col-span-12" />
              <HostIncidentsCard hostId={hostId} className="col-span-6 max-[999px]:col-span-12" />
              <MaintenanceCard host={host} className="col-span-6 max-[999px]:col-span-12" />
              <ProbeCard host={host} probes={probes} className="col-span-6 max-[999px]:col-span-12" />
            </div>
            {host.agent && <AgentSection agent={host.agent} stale={stale} />}
            {!host.agent && host.integration && <DeviceSection host={host} device={device} />}
            {hasPorts && <PortSummaryCard ports={portTable} onShowAll={() => setTab('ports')} />}
            <DiscoveredPortsCard host={host} />
          </TabPanel>

          {hasPorts && (
            <TabPanel idBase={TAB_BASE} id="ports" active={tab === 'ports'}>
              <PortsTab ports={portTable} clients={Array.isArray(device?.connected_clients) ? device.connected_clients : []} />
            </TabPanel>
          )}
          <TabPanel idBase={TAB_BASE} id="timeline" active={tab === 'timeline'}>
            <HostTimeline hostId={hostId} />
          </TabPanel>
          <TabPanel idBase={TAB_BASE} id="syslog" active={tab === 'syslog'}>
            <HostSyslog hostId={hostId} />
          </TabPanel>

          <HostFormModal
            open={showEdit}
            mode="edit"
            host={host}
            onClose={() => setShowEdit(false)}
            onSaved={() => {
              qc.invalidateQueries({ queryKey: ['host', hostId] });
              qc.invalidateQueries({ queryKey: ['hosts-v1'] });
              qc.invalidateQueries({ queryKey: ['hosts'] });
              toast('Host saved', 'success');
            }}
          />
        </>
      )}
    </div>
  );
}
