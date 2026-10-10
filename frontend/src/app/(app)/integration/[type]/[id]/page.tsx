'use client';

import { useEffect } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { AlertTriangle, ArrowLeft, Database } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { Card, CardHeader } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import { Tag } from '@/components/ui/Tag';
import { buttonClasses } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryErrorState, StaleDataBanner } from '@/components/ui/QueryState';
import { useIntegration } from '@/hooks/queries/useIntegrations';
import { timeAgo } from '@/lib/utils';
import { ProxmoxDetail } from '@/components/integrations/ProxmoxDetail';
import { UnifiDetail } from '@/components/integrations/UnifiDetail';
import { PiholeDetail } from '@/components/integrations/PiholeDetail';
import { PortainerDetail } from '@/components/integrations/PortainerDetail';
import { SynologyDetail } from '@/components/integrations/SynologyDetail';
import { UnasDetail } from '@/components/integrations/UnasDetail';
import { SpeedtestDetail } from '@/components/integrations/SpeedtestDetail';
import { PhpipamDetail } from '@/components/integrations/PhpipamDetail';
import { AdguardDetail } from '@/components/integrations/AdguardDetail';
import { TruenasDetail } from '@/components/integrations/TruenasDetail';
import { FirewallDetail } from '@/components/integrations/FirewallDetail';
import { HassDetail } from '@/components/integrations/HassDetail';
import { GiteaDetail } from '@/components/integrations/GiteaDetail';
import { UpsDetail } from '@/components/integrations/UpsDetail';
import { RedfishDetail } from '@/components/integrations/RedfishDetail';
import { SwisscomDetail } from '@/components/integrations/SwisscomDetail';
import { CloudflareDetail } from '@/components/integrations/CloudflareDetail';
import { NpmDetail } from '@/components/integrations/NpmDetail';
import { TechnitiumDetail } from '@/components/integrations/TechnitiumDetail';
import { integrationHealth } from '../../_components/health';

/* eslint-disable @typescript-eslint/no-explicit-any */
const detailComponents: Record<string, React.ComponentType<{ data: any; configId?: number }>> = {
  proxmox: ProxmoxDetail,
  unifi: UnifiDetail,
  pihole: PiholeDetail,
  portainer: PortainerDetail,
  synology: SynologyDetail,
  unas: UnasDetail,
  speedtest: SpeedtestDetail,
  phpipam: PhpipamDetail,
  adguard: AdguardDetail,
  truenas: TruenasDetail,
  firewall: FirewallDetail,
  hass: HassDetail,
  gitea: GiteaDetail,
  ups: UpsDetail,
  redfish: RedfishDetail,
  swisscom: SwisscomDetail,
  cloudflare: CloudflareDetail,
  npm: NpmDetail,
  technitium: TechnitiumDetail,
};
/* eslint-enable @typescript-eslint/no-explicit-any */

/** The detail endpoint also returns these (not in the shared type yet). */
interface SnapshotExtras {
  name?: string;
  enabled?: boolean;
}

export default function IntegrationDetailPage() {
  const params = useParams();
  const type = params.type as string;
  const id = Number(params.id);
  const query = useIntegration(id);
  const { data: snapshot, isLoading } = query;
  const extras = (snapshot ?? {}) as SnapshotExtras;

  const typeLabel = type.charAt(0).toUpperCase() + type.slice(1);
  const name = extras.name ?? `${typeLabel} #${id}`;
  useEffect(() => { document.title = `${name} | Nodeglow`; }, [name]);

  const DetailComponent = detailComponents[type];
  const timestamp = snapshot?.timestamp || null;
  const health = snapshot
    ? integrationHealth(
        { type, enabled: extras.enabled ?? true, ok: snapshot.ok, is_standby: snapshot.is_standby },
        timestamp,
      )
    : null;

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Integrations', href: '/integration/store' }, { label: typeLabel, href: `/integration/${type}` }, { label: name }]} />
      <PageHeader
        title={isLoading ? 'Loading…' : name}
        status={health ? <StatusPill status={health.status}>{health.label}</StatusPill> : undefined}
        description={
          health ? (
            <>
              {health.reason}
              {timestamp && (
                <>
                  {' · '}
                  <time dateTime={timestamp} title={new Date(timestamp).toLocaleString()}>
                    {health.lastOk ? 'last successful collection' : 'last attempt'} {timeAgo(timestamp)}
                  </time>
                </>
              )}
            </>
          ) : typeLabel
        }
        actions={
          <Link href={`/integration/${type}`} className={buttonClasses({ variant: 'ghost', size: 'sm' })}>
            <ArrowLeft size={14} aria-hidden="true" /> All {typeLabel} instances
          </Link>
        }
      />

      {query.isError && !snapshot ? (
        <Card><QueryErrorState error={query.error} onRetry={query.refetch} title="Could not load this integration" /></Card>
      ) : (
        <>
          {query.isError && snapshot && <StaleDataBanner error={query.error} onRetry={query.refetch} updatedAt={query.dataUpdatedAt} />}

          {snapshot && (
            <Card className="mb-4" padding="sm">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                <Fact label="Type"><span className="font-mono">{snapshot.entity_type}</span></Fact>
                <Fact label="Last collection">
                  {timestamp ? new Date(timestamp).toLocaleString() : 'Never'}
                </Fact>
                <Fact label="Result">
                  {!timestamp ? '—' : snapshot.ok ? 'Succeeded' : <span className="text-down">Failed</span>}
                </Fact>
                <Fact label="Cluster group">
                  {snapshot.cluster_group ? <Tag className="font-mono">{snapshot.cluster_group}</Tag> : '—'}
                </Fact>
              </dl>
            </Card>
          )}

          {snapshot?.error && (
            <Card className="mb-4 border-down/30" role="alert">
              <div className="flex items-start gap-3">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-down" aria-hidden="true" />
                <div className="min-w-0">
                  <h2 className="mb-1 text-body font-medium text-fg">Last collection failed</h2>
                  <p className="break-words font-mono text-meta text-down">{snapshot.error}</p>
                  <p className="mt-2 text-meta text-fg-2">
                    {snapshot.data_json ? 'Data below may be incomplete or outdated.' : 'No data was collected in this attempt.'}
                  </p>
                </div>
              </div>
            </Card>
          )}

          {isLoading ? (
            <Card aria-busy="true" aria-label="Loading">
              <div className="space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            </Card>
          ) : snapshot?.data_json && DetailComponent ? (
            <DetailComponent data={snapshot.data_json} configId={id} />
          ) : snapshot?.data_json ? (
            <Card>
              <CardHeader title="Snapshot data" />
              <pre className="max-h-[500px] overflow-auto rounded-ctl bg-surface-2 p-4 font-mono text-meta text-fg">
                {JSON.stringify(snapshot.data_json, null, 2)}
              </pre>
            </Card>
          ) : (
            <Card>
              <EmptyState
                icon={Database}
                title="No data collected yet"
                description={
                  snapshot?.error
                    ? 'Fix the error above; data appears after the next successful collection.'
                    : 'Data appears after the first successful collection.'
                }
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta text-fg-3">{label}</dt>
      <dd className="truncate text-ui text-fg">{children}</dd>
    </div>
  );
}
