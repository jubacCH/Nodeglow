'use client';

import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { StatGrid, StatTile, StateLabel, TableCard, UsageBar, isNum } from './parts';

interface HetznerServer {
  id: number | null;
  name: string;
  status: string;
  server_type: string;
  datacenter: string;
  location: string;
  ipv4: string;
  ipv6: string;
  ingoing_traffic: number | null;
  outgoing_traffic: number | null;
  included_traffic: number | null;
  traffic_pct: number | null;
  protect_delete: boolean | null;
  protect_rebuild: boolean | null;
  locked: boolean | null;
}

interface HetznerVolume {
  id: number | null;
  name: string;
  status: string;
  size_gb: number | null;
  location: string;
  /** null: the API did not say. */
  attached: boolean | null;
}

interface HetznerTarget {
  label: string;
  /** null: health unknown. */
  healthy: boolean | null;
  unhealthy_ports: number[];
}

interface HetznerLoadBalancer {
  id: number | null;
  name: string;
  type: string;
  location: string;
  ipv4: string;
  targets: HetznerTarget[];
  healthy_targets: number;
  unhealthy_targets: number;
}

interface HetznerIp {
  id: number | null;
  name: string;
  ip: string;
  type: string;
  location: string;
  assigned: boolean | null;
}

interface HetznerData {
  server_count: number;
  servers: HetznerServer[];
  volumes: HetznerVolume[];
  load_balancers: HetznerLoadBalancer[];
  floating_ips: HetznerIp[];
  primary_ips: HetznerIp[];
  unavailable?: string[];
}

const TRANSIENT = new Set(['initializing', 'starting', 'stopping', 'migrating', 'rebuilding', 'deleting']);

function serverState(status: string | null | undefined): HealthState {
  if (status === 'running') return 'ok';
  if (status === 'off') return 'warning';
  if (status && TRANSIENT.has(status)) return 'degraded';
  return 'unknown';
}

/** Hetzner's "20 TB" quota is 20 * 1024^4 bytes in the API (21990232555520), so
 *  binary steps under Hetzner's own unit names keep the figures matching theirs. */
function decimalBytes(v: unknown): string | null {
  if (!isNum(v)) return null;
  if (v <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(Math.floor(Math.log(v) / Math.log(1024)), units.length - 1);
  return `${(v / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function trafficState(pct: number | null | undefined): HealthState | undefined {
  // Mirrors the default alert limits (80 / 95 %); the bar only colours.
  if (!isNum(pct)) return undefined;
  if (pct >= 95) return 'down';
  if (pct >= 80) return 'warning';
  return 'ok';
}

function ipTable(title: string, rows: HetznerIp[]) {
  return (
    <TableCard title={title} meta={`${rows.length}`}>
      <Table>
        <THead>
          <Tr>
            <Th>Name</Th>
            <Th>Address</Th>
            <Th>Type</Th>
            <Th>Location</Th>
            <Th>Assigned</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((r, i) => (
            <Tr key={r.id ?? i}>
              <Td>{r.name || '—'}</Td>
              <Td className="font-mono text-meta">{r.ip || '—'}</Td>
              <Td>{r.type ? <Badge>{r.type}</Badge> : '—'}</Td>
              <Td muted>{r.location || '—'}</Td>
              <Td>
                {r.assigned === null || r.assigned === undefined
                  ? '—'
                  : r.assigned
                    ? <Badge>Assigned</Badge>
                    : <Badge tone="warning">Unassigned</Badge>}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableCard>
  );
}

export function HetznerDetail({ data }: { data: HetznerData }) {
  if (!data) return null;
  const missing = new Set(data.unavailable ?? []);
  const servers = data.servers ?? [];
  const volumes = data.volumes ?? [];
  const lbs = data.load_balancers ?? [];
  const floating = data.floating_ips ?? [];
  const primary = data.primary_ips ?? [];

  const running = servers.filter((s) => s.status === 'running').length;
  const notRunning = servers.length - running;
  const unattached = volumes.filter((v) => v.attached === false).length;
  const unhealthy = lbs.reduce((n, lb) => n + (isNum(lb.unhealthy_targets) ? lb.unhealthy_targets : 0), 0);

  return (
    <div className="space-y-6">
      {missing.size > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Could not read: <span className="text-fg">{[...missing].join(', ')}</span>. These values show as unknown.
          </p>
        </Card>
      )}

      <StatGrid cols={5}>
        <StatTile label="Servers" value={servers.length} />
        <StatTile
          label="Not running"
          value={notRunning}
          state={notRunning > 0 ? 'warning' : 'ok'}
        />
        <StatTile
          label="Volumes"
          value={missing.has('volumes') ? null : volumes.length}
          state={unattached > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="Load balancers"
          value={missing.has('load balancers') ? null : lbs.length}
        />
        <StatTile
          label="Unhealthy targets"
          value={missing.has('load balancers') ? null : unhealthy}
          state={missing.has('load balancers') ? undefined : unhealthy > 0 ? 'down' : 'ok'}
        />
      </StatGrid>

      <TableCard title="Servers" meta={`${servers.length}`}>
        <Table>
          <THead>
            <Tr>
              <Th>Name</Th>
              <Th>Status</Th>
              <Th>Type</Th>
              <Th>Location</Th>
              <Th>IPv4</Th>
              <Th>Outgoing traffic</Th>
              <Th>Flags</Th>
            </Tr>
          </THead>
          <TBody>
            {servers.map((s, i) => (
              <Tr key={s.id ?? i}>
                <Td className="whitespace-nowrap font-medium">{s.name || '—'}</Td>
                <Td><StateLabel status={serverState(s.status)}>{s.status || '—'}</StateLabel></Td>
                <Td>{s.server_type ? <Badge>{s.server_type}</Badge> : '—'}</Td>
                <Td muted className="whitespace-nowrap">{s.location || s.datacenter || '—'}</Td>
                <Td className="font-mono text-meta">{s.ipv4 || '—'}</Td>
                <Td className="min-w-[180px]">
                  <UsageBar
                    label="Quota"
                    pct={s.traffic_pct}
                    state={trafficState(s.traffic_pct)}
                    detail={`${decimalBytes(s.outgoing_traffic) ?? '—'} / ${decimalBytes(s.included_traffic) ?? '—'}`}
                  />
                </Td>
                <Td>
                  <span className="flex flex-wrap gap-1">
                    {s.locked === true && <Badge tone="maint">Locked</Badge>}
                    {s.protect_delete === true && <Badge>Delete protected</Badge>}
                    {s.protect_rebuild === true && <Badge>Rebuild protected</Badge>}
                  </span>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableCard>

      {!missing.has('volumes') && volumes.length > 0 && (
        <TableCard title="Volumes" meta={`${volumes.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th numeric>Size</Th>
                <Th>Location</Th>
                <Th>Attached</Th>
              </Tr>
            </THead>
            <TBody>
              {volumes.map((v, i) => (
                <Tr key={v.id ?? i}>
                  <Td className="font-medium">{v.name || '—'}</Td>
                  <Td>
                    <StateLabel status={v.status === 'available' ? 'ok' : v.status === 'creating' ? 'degraded' : 'unknown'}>
                      {v.status || '—'}
                    </StateLabel>
                  </Td>
                  <Td numeric>{isNum(v.size_gb) ? `${v.size_gb} GB` : '—'}</Td>
                  <Td muted>{v.location || '—'}</Td>
                  <Td>
                    {v.attached === null || v.attached === undefined
                      ? '—'
                      : v.attached
                        ? <Badge>Attached</Badge>
                        : <Badge tone="warning">Not attached</Badge>}
                  </Td>
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
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>IPv4</Th>
                <Th>Target health</Th>
              </Tr>
            </THead>
            <TBody>
              {lbs.map((lb, i) => {
                const targets = lb.targets ?? [];
                const unknown = targets.filter((t) => t.healthy === null || t.healthy === undefined).length;
                return (
                  <Tr key={lb.id ?? i}>
                    <Td className="font-medium">{lb.name || '—'}</Td>
                    <Td>{lb.type ? <Badge>{lb.type}</Badge> : '—'}</Td>
                    <Td className="font-mono text-meta">{lb.ipv4 || '—'}</Td>
                    <Td>
                      {targets.length === 0 ? (
                        '—'
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          <Badge tone={lb.healthy_targets > 0 ? 'ok' : 'neutral'}>{lb.healthy_targets} healthy</Badge>
                          {lb.unhealthy_targets > 0 && <Badge tone="down">{lb.unhealthy_targets} unhealthy</Badge>}
                          {unknown > 0 && <Badge tone="unknown">{unknown} unknown</Badge>}
                        </span>
                      )}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableCard>
      )}

      {!missing.has('floating IPs') && floating.length > 0 && ipTable('Floating IPs', floating)}
      {!missing.has('primary IPs') && primary.length > 0 && ipTable('Primary IPs', primary)}
    </div>
  );
}
