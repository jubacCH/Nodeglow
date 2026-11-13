'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { EnabledPill, KV, KVGrid, SectionTitle, StatGrid, StatTile, TableCard, UsageBar, formatBytes, isNum } from './parts';

interface VsphereVersion {
  version: string | null;
  build: string | null;
  product: string | null;
  summary: string | null;
  release_date: string | null;
}

interface VsphereHost {
  id: string;
  name: string;
  connection_state: string | null;
  power_state: string | null;
}

interface VsphereVm {
  id: string;
  name: string;
  power_state: string | null;
  cpu_count: number | null;
  memory_mib: number | null;
}

interface VsphereDatastore {
  id: string;
  name: string;
  type: string | null;
  capacity: number | null;
  free_space: number | null;
  used: number | null;
  pct: number | null;
}

interface VsphereCluster {
  id: string;
  name: string;
  ha_enabled: boolean | null;
  drs_enabled: boolean | null;
}

interface VsphereTotals {
  hosts_total: number | null;
  hosts_connected: number | null;
  vms_total: number | null;
  vms_powered_on: number | null;
  datastores_total: number | null;
  clusters_total: number | null;
}

interface VsphereData {
  collected_at?: string;
  version: VsphereVersion | null;
  hosts: VsphereHost[] | null;
  vms: VsphereVm[] | null;
  vm_list_error: string | null;
  datastores: VsphereDatastore[] | null;
  clusters: VsphereCluster[] | null;
  appliance_health: Record<string, string | null> | null;
  totals?: VsphereTotals;
  unavailable?: string[];
}

const HEALTH_LABELS: Record<string, string> = {
  system: 'Overall',
  storage: 'Storage',
  database_storage: 'Database storage',
  load: 'Load',
  memory: 'Memory',
  swap: 'Swap',
  software_packages: 'Software packages',
  applmgmt: 'Appliance management',
};

function hostState(s: string | null | undefined): HealthState {
  if (s === 'CONNECTED') return 'ok';
  if (s === 'DISCONNECTED' || s === 'NOT_RESPONDING') return 'down';
  if (s) return 'warning';
  return 'unknown';
}

function healthState(v: string | null | undefined): HealthState {
  if (v === 'green') return 'ok';
  if (v === 'red') return 'down';
  if (v === 'yellow' || v === 'orange') return 'warning';
  return 'unknown';
}

function pretty(v: string | null | undefined): string | undefined {
  return v ? v.replace(/_/g, ' ').toLowerCase() : undefined;
}

export function VsphereDetail({ data }: { data: VsphereData }) {
  const { version, hosts, vms, datastores, clusters, appliance_health } = data;
  const totals = data.totals ?? ({} as Partial<VsphereTotals>);
  const unavailable = data.unavailable ?? [];
  const hostsLine = isNum(totals.hosts_connected) && isNum(totals.hosts_total)
    ? `${totals.hosts_connected}/${totals.hosts_total}`
    : null;
  const vmsLine = isNum(totals.vms_powered_on) && isNum(totals.vms_total)
    ? `${totals.vms_powered_on}/${totals.vms_total}`
    : null;
  const healthEntries = Object.entries(appliance_health ?? {});

  return (
    <div className="space-y-6">
      {(unavailable.length > 0 || data.vm_list_error) && (
        <Card as="section">
          <CardHeader title="Not available" meta={`${unavailable.length}`} />
          <ul className="space-y-1 text-ui text-fg-2">
            {unavailable.map((u) => (
              <li key={u} className="wrap-break-word">{u}</li>
            ))}
            {data.vm_list_error && <li className="wrap-break-word">{data.vm_list_error}</li>}
          </ul>
        </Card>
      )}

      {version && (
        <Card as="section">
          <CardHeader title="vCenter" />
          <KVGrid className="md:grid-cols-3">
            <KV label="Product">{version.product}</KV>
            <KV label="Version" mono>{version.version}</KV>
            <KV label="Build" mono>{version.build}</KV>
          </KVGrid>
        </Card>
      )}

      <StatGrid>
        <StatTile
          label="Hosts connected"
          value={hostsLine}
          state={isNum(totals.hosts_connected) && isNum(totals.hosts_total) && totals.hosts_connected < totals.hosts_total ? 'down' : undefined}
        />
        <StatTile label="VMs powered on" value={vmsLine} />
        <StatTile label="Datastores" value={totals.datastores_total} />
        <StatTile label="Clusters" value={totals.clusters_total} />
      </StatGrid>

      {/* Appliance health */}
      {healthEntries.length > 0 && (
        <section>
          <SectionTitle>Appliance health</SectionTitle>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {healthEntries.map(([key, value]) => (
              <Card key={key} padding="sm">
                <div className="flex min-w-0 flex-col gap-2">
                  <span className="truncate text-meta text-fg-3">{HEALTH_LABELS[key] ?? key}</span>
                  <StatusPill status={healthState(value)}>{value || undefined}</StatusPill>
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Hosts */}
      {hosts && hosts.length > 0 && (
        <TableCard title="Hosts" meta={`${hosts.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Connection</Th>
                <Th>Power</Th>
              </Tr>
            </THead>
            <TBody>
              {hosts.map((h) => (
                <Tr key={h.id || h.name}>
                  <Td className="whitespace-nowrap font-mono">{h.name}</Td>
                  <Td><StatusPill status={hostState(h.connection_state)} size="sm">{pretty(h.connection_state)}</StatusPill></Td>
                  <Td muted>{pretty(h.power_state) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Datastores */}
      {datastores && datastores.length > 0 && (
        <section>
          <SectionTitle meta={`${datastores.length}`}>Datastores</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {datastores.map((d) => (
              <Card key={d.id || d.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{d.name}</span>
                  {d.type && <Badge>{d.type}</Badge>}
                </div>
                <UsageBar
                  label="Usage"
                  pct={d.pct}
                  detail={isNum(d.pct) && isNum(d.used) && isNum(d.capacity)
                    ? `${formatBytes(d.used)} / ${formatBytes(d.capacity)} (${d.pct.toFixed(1)} %)`
                    : undefined}
                />
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Clusters */}
      {clusters && clusters.length > 0 && (
        <TableCard title="Clusters" meta={`${clusters.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>HA</Th>
                <Th>DRS</Th>
              </Tr>
            </THead>
            <TBody>
              {clusters.map((c) => (
                <Tr key={c.id || c.name}>
                  <Td className="whitespace-nowrap">{c.name}</Td>
                  <Td><EnabledPill enabled={c.ha_enabled} /></Td>
                  <Td><EnabledPill enabled={c.drs_enabled} /></Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* VMs */}
      {vms && vms.length > 0 && (
        <TableCard title="Virtual machines" meta={`${vms.length}`} maxHeight={480}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Power</Th>
                <Th numeric>vCPU</Th>
                <Th numeric>Memory</Th>
              </Tr>
            </THead>
            <TBody>
              {vms.map((v) => (
                <Tr key={v.id || v.name}>
                  <Td className="whitespace-nowrap">{v.name}</Td>
                  <Td muted>{pretty(v.power_state) ?? '—'}</Td>
                  <Td numeric>{isNum(v.cpu_count) ? v.cpu_count : '—'}</Td>
                  <Td numeric className="whitespace-nowrap">
                    {isNum(v.memory_mib) ? formatBytes(v.memory_mib * 1024 * 1024) : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
