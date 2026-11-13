'use client';

import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { StatGrid, StatTile, StateLabel, TableCard, isNum } from './parts';

interface ExoscaleInstance {
  id: string | null;
  name: string;
  state: string;
  type: string;
  public_ip: string;
}

interface ExoscaleSksCluster {
  id: string | null;
  name: string;
  state: string;
  version: string;
  level: string;
  nodepool_count: number | null;
}

interface ExoscaleDbaasService {
  name: string;
  type: string;
  state: string;
  plan: string;
  version: string;
}

interface ExoscaleNlbService {
  id: string | null;
  name: string;
  state: string;
  protocol: string;
  port: number | null;
  healthy: number;
  unhealthy: number;
  unknown: number;
}

interface ExoscaleLoadBalancer {
  id: string | null;
  name: string;
  state: string;
  ip: string;
  services: ExoscaleNlbService[];
}

interface ExoscaleData {
  zone?: string;
  instances: ExoscaleInstance[];
  sks_clusters: ExoscaleSksCluster[];
  dbaas_services: ExoscaleDbaasService[];
  load_balancers: ExoscaleLoadBalancer[];
  unavailable?: string[];
}

/** Resource state → health: only "running" is green, "error" is down. */
function resourceState(state: string | null | undefined): HealthState {
  if (!state || state === 'unknown') return 'unknown';
  if (state === 'running') return 'ok';
  if (state === 'error') return 'down';
  return 'warning';
}

function listener(s: ExoscaleNlbService): string {
  const text = `${s.protocol || ''}${isNum(s.port) ? `:${s.port}` : ''}`;
  return text || '—';
}

function ServiceHealth({ svc }: { svc: ExoscaleNlbService }) {
  const total = svc.healthy + svc.unhealthy + svc.unknown;
  if (total === 0) return <span className="text-fg-3">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      <Badge tone={svc.healthy > 0 ? 'ok' : 'neutral'}>{svc.healthy} healthy</Badge>
      {svc.unhealthy > 0 && <Badge tone="down">{svc.unhealthy} unhealthy</Badge>}
      {svc.unknown > 0 && <Badge tone="unknown">{svc.unknown} unknown</Badge>}
    </span>
  );
}

export function ExoscaleDetail({ data }: { data: ExoscaleData }) {
  if (!data) return null;
  const missing = new Set(data.unavailable ?? []);
  const instances = data.instances ?? [];
  const sks = data.sks_clusters ?? [];
  const dbaas = data.dbaas_services ?? [];
  const lbs = data.load_balancers ?? [];

  const notRunning = (items: { state: string }[]) => items.filter((i) => i.state !== 'running').length;
  const unhealthy = lbs.reduce(
    (n, lb) => n + (lb.services ?? []).reduce((m, s) => m + (isNum(s.unhealthy) ? s.unhealthy : 0), 0),
    0,
  );

  return (
    <div className="space-y-6">
      {missing.size > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Could not read: <span className="text-fg">{[...missing].join(', ')}</span>. These values show as unknown.
          </p>
        </Card>
      )}

      <StatGrid cols={4}>
        <StatTile
          label="Instances"
          value={instances.length}
          unit={data.zone ? `in ${data.zone}` : undefined}
          state={notRunning(instances) > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="SKS clusters"
          value={missing.has('SKS clusters') ? null : sks.length}
          state={!missing.has('SKS clusters') && notRunning(sks) > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="DBaaS services"
          value={missing.has('DBaaS services') ? null : dbaas.length}
          state={!missing.has('DBaaS services') && notRunning(dbaas) > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="Unhealthy LB targets"
          value={missing.has('load balancers') ? null : unhealthy}
          state={missing.has('load balancers') ? undefined : unhealthy > 0 ? 'down' : 'ok'}
        />
      </StatGrid>

      <TableCard title="Instances" meta={`${instances.length}`}>
        <Table>
          <THead>
            <Tr>
              <Th>Name</Th>
              <Th>State</Th>
              <Th>Type</Th>
              <Th>Public IP</Th>
            </Tr>
          </THead>
          <TBody>
            {instances.map((i, idx) => (
              <Tr key={i.id ?? idx}>
                <Td className="font-medium">{i.name || '—'}</Td>
                <Td><StateLabel status={resourceState(i.state)}>{i.state || '—'}</StateLabel></Td>
                <Td>{i.type ? <Badge>{i.type}</Badge> : '—'}</Td>
                <Td className="font-mono text-meta">{i.public_ip || '—'}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableCard>

      {!missing.has('SKS clusters') && sks.length > 0 && (
        <TableCard title="SKS clusters" meta={`${sks.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Version</Th>
                <Th>Level</Th>
                <Th numeric>Node pools</Th>
              </Tr>
            </THead>
            <TBody>
              {sks.map((c, idx) => (
                <Tr key={c.id ?? idx}>
                  <Td className="font-medium">{c.name || '—'}</Td>
                  <Td><StateLabel status={resourceState(c.state)}>{c.state || '—'}</StateLabel></Td>
                  <Td className="font-mono text-meta">{c.version || '—'}</Td>
                  <Td muted>{c.level || '—'}</Td>
                  <Td numeric>{isNum(c.nodepool_count) ? c.nodepool_count : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {!missing.has('DBaaS services') && dbaas.length > 0 && (
        <TableCard title="DBaaS services" meta={`${dbaas.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Type</Th>
                <Th>Plan</Th>
                <Th>Version</Th>
              </Tr>
            </THead>
            <TBody>
              {dbaas.map((d, idx) => (
                <Tr key={`${d.name}-${idx}`}>
                  <Td className="font-medium">{d.name || '—'}</Td>
                  <Td><StateLabel status={resourceState(d.state)}>{d.state || '—'}</StateLabel></Td>
                  <Td>{d.type ? <Badge>{d.type}</Badge> : '—'}</Td>
                  <Td muted>{d.plan || '—'}</Td>
                  <Td className="font-mono text-meta">{d.version || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {!missing.has('load balancers') && lbs.length > 0 && (
        <TableCard title="Load balancers" meta={`${lbs.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Load balancer</Th>
                <Th>State</Th>
                <Th>Service</Th>
                <Th>Listener</Th>
                <Th>Target health</Th>
              </Tr>
            </THead>
            <TBody>
              {lbs.flatMap((lb, li) => {
                const services = lb.services ?? [];
                if (services.length === 0) {
                  return [
                    <Tr key={`${lb.id ?? li}-none`}>
                      <Td className="font-medium">{lb.name || '—'}</Td>
                      <Td><StateLabel status={resourceState(lb.state)}>{lb.state || '—'}</StateLabel></Td>
                      <Td muted>—</Td>
                      <Td muted>—</Td>
                      <Td muted>—</Td>
                    </Tr>,
                  ];
                }
                return services.map((s, si) => (
                  <Tr key={`${lb.id ?? li}-${s.id ?? si}`}>
                    <Td className="font-medium">{si === 0 ? lb.name || '—' : ''}</Td>
                    <Td>
                      {si === 0 ? <StateLabel status={resourceState(lb.state)}>{lb.state || '—'}</StateLabel> : null}
                    </Td>
                    <Td>{s.name || '—'}</Td>
                    <Td muted className="whitespace-nowrap font-mono text-meta">
                      {listener(s)}
                    </Td>
                    <Td><ServiceHealth svc={s} /></Td>
                  </Tr>
                ));
              })}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
