'use client';

import Link from 'next/link';
import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { KV, KVGrid, StatGrid, StatTile, TableCard, formatDate, isNum } from './parts';

interface NetboxDevice {
  id?: number | null;
  name: string;
  status: string;
  role?: string;
  type?: string;
  site?: string;
  tenant?: string;
  ip4?: string;
  ip6?: string;
  ip?: string;
}

interface NetboxVm {
  id?: number | null;
  name: string;
  status: string;
  role?: string;
  cluster?: string;
  site?: string;
  tenant?: string;
  ip?: string;
}

interface NetboxPrefix {
  id?: number | null;
  prefix: string;
  status: string;
  vrf?: string;
  scope?: string;
  tenant?: string;
  description?: string;
  utilization: number | null;
}

interface NetboxSite {
  id?: number | null;
  name: string;
  status: string;
  region?: string;
  tenant?: string;
  device_count?: number | null;
}

interface NetboxData {
  collected_at?: string;
  source_url?: string;
  version?: string;
  python_version?: string;
  workers_running?: number | null;
  totals?: Partial<Record<'sites' | 'devices' | 'vms' | 'prefixes', number | null>>;
  device_status?: Record<string, number> | null;
  vm_status?: Record<string, number> | null;
  sites?: NetboxSite[] | null;
  devices?: NetboxDevice[] | null;
  vms?: NetboxVm[] | null;
  prefixes?: NetboxPrefix[] | null;
  failed_devices?: NetboxDevice[] | null;
  truncated?: Partial<Record<string, boolean>>;
  unavailable?: string[];
}

const LABELS: Record<string, string> = { sites: 'sites', devices: 'devices', vms: 'virtual machines', prefixes: 'prefixes' };

function statusTone(status: string): 'ok' | 'warning' | 'down' | 'neutral' {
  if (status === 'active') return 'ok';
  if (status === 'failed') return 'down';
  if (status === 'offline' || status === 'decommissioning') return 'warning';
  return 'neutral';
}

function StatusBadge({ status }: { status: string }) {
  if (!status) return <span className="text-fg-3">—</span>;
  return <Badge tone={statusTone(status)}>{status}</Badge>;
}

function HostLink({ value }: { value?: string }) {
  if (!value) return <span className="text-fg-3">—</span>;
  return <Link href={'/hosts?q=' + encodeURIComponent(value)} className="text-accent hover:underline">{value}</Link>;
}

function StatusCounts({ title, counts, partial }: { title: string; counts: Record<string, number> | null | undefined; partial: boolean }) {
  const entries = counts ? Object.entries(counts) : [];
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-meta text-fg-2">
      <span className="text-fg-3">{title}{partial ? ' (partial)' : ''}:</span>
      {entries.map(([status, n]) => (
        <span key={status} className="inline-flex items-center gap-1">
          <StatusBadge status={status} />
          <span className="num">{n}</span>
        </span>
      ))}
    </div>
  );
}

function tableMeta(shown: number, total: number | null | undefined): string {
  return isNum(total) && total > shown ? `${shown} of ${total}` : `${shown}`;
}

export function NetboxDetail({ data }: { data: NetboxData }) {
  const totals = data.totals ?? {};
  const unavailable = data.unavailable ?? [];
  const truncated = Object.entries(data.truncated ?? {}).filter(([, v]) => v === true).map(([k]) => LABELS[k] ?? k);
  const devices = data.devices ?? null;
  const vms = data.vms ?? null;
  const prefixes = data.prefixes ?? null;
  const sites = data.sites ?? null;
  const failed = data.failed_devices ?? null;

  return (
    <div className="space-y-6">
      {unavailable.length > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Could not read: <span className="text-fg">{unavailable.join(', ')}</span>. These values show as unknown.
          </p>
        </Card>
      )}

      {truncated.length > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Only the first part of the list is stored for <span className="text-fg">{truncated.join(', ')}</span>.
            Totals come from NetBox and are complete; status counts and tables are partial.
          </p>
        </Card>
      )}

      <StatGrid cols={5}>
        <StatTile label="Sites" value={totals.sites} />
        <StatTile label="Devices" value={totals.devices} />
        <StatTile label="Virtual machines" value={totals.vms} />
        <StatTile label="Prefixes" value={totals.prefixes} />
        <StatTile
          label="Failed devices"
          value={failed ? failed.length : null}
          state={failed && failed.length > 0 ? 'warning' : undefined}
        />
      </StatGrid>

      <div className="space-y-2">
        <StatusCounts title="Devices" counts={data.device_status} partial={data.truncated?.devices === true} />
        <StatusCounts title="Virtual machines" counts={data.vm_status} partial={data.truncated?.vms === true} />
      </div>

      {failed && failed.length > 0 && (
        <TableCard title="Devices marked failed" meta={`${failed.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Site</Th>
                <Th>Role</Th>
                <Th>Primary IP</Th>
              </Tr>
            </THead>
            <TBody>
              {failed.map((d) => (
                <Tr key={`${d.id ?? ''}-${d.name}`}>
                  <Td className="font-mono">{d.name || '—'}</Td>
                  <Td muted>{d.site || '—'}</Td>
                  <Td muted>{d.role || '—'}</Td>
                  <Td className="font-mono"><HostLink value={d.ip} /></Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {devices && devices.length > 0 && (
        <TableCard title="Devices" meta={tableMeta(devices.length, totals.devices)} maxHeight={420}>
          <Table>
            <THead sticky>
              <Tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Role</Th>
                <Th>Site</Th>
                <Th>Tenant</Th>
                <Th>Primary IP</Th>
              </Tr>
            </THead>
            <TBody>
              {devices.map((d) => (
                <Tr key={`${d.id ?? ''}-${d.name}`}>
                  <Td className="max-w-[260px] truncate font-mono">{d.name || '—'}</Td>
                  <Td><StatusBadge status={d.status} /></Td>
                  <Td muted>{d.role || '—'}</Td>
                  <Td muted>{d.site || '—'}</Td>
                  <Td muted>{d.tenant || '—'}</Td>
                  <Td className="whitespace-nowrap font-mono"><HostLink value={d.ip} /></Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {vms && vms.length > 0 && (
        <TableCard title="Virtual machines" meta={tableMeta(vms.length, totals.vms)} maxHeight={420}>
          <Table>
            <THead sticky>
              <Tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Cluster</Th>
                <Th>Role</Th>
                <Th>Tenant</Th>
                <Th>Primary IP</Th>
              </Tr>
            </THead>
            <TBody>
              {vms.map((v) => (
                <Tr key={`${v.id ?? ''}-${v.name}`}>
                  <Td className="max-w-[260px] truncate font-mono">{v.name || '—'}</Td>
                  <Td><StatusBadge status={v.status} /></Td>
                  <Td muted>{v.cluster || '—'}</Td>
                  <Td muted>{v.role || '—'}</Td>
                  <Td muted>{v.tenant || '—'}</Td>
                  <Td className="whitespace-nowrap font-mono"><HostLink value={v.ip} /></Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {prefixes && prefixes.length > 0 && (
        <TableCard title="Prefixes" meta={tableMeta(prefixes.length, totals.prefixes)} maxHeight={420}>
          <Table>
            <THead sticky>
              <Tr>
                <Th>Prefix</Th>
                <Th>Status</Th>
                <Th>VRF</Th>
                <Th>Scope</Th>
                <Th>Description</Th>
                <Th numeric>Utilisation</Th>
              </Tr>
            </THead>
            <TBody>
              {prefixes.map((p) => (
                <Tr key={`${p.id ?? ''}-${p.prefix}`}>
                  <Td className="whitespace-nowrap font-mono">{p.prefix || '—'}</Td>
                  <Td><StatusBadge status={p.status} /></Td>
                  <Td muted>{p.vrf || '—'}</Td>
                  <Td muted>{p.scope || '—'}</Td>
                  <Td muted className="max-w-[240px] truncate">{p.description || '—'}</Td>
                  <Td numeric muted>{isNum(p.utilization) ? `${p.utilization.toFixed(1)} %` : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {sites && sites.length > 0 && (
        <TableCard title="Sites" meta={tableMeta(sites.length, totals.sites)} maxHeight={420}>
          <Table>
            <THead sticky>
              <Tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Region</Th>
                <Th>Tenant</Th>
                <Th numeric>Devices</Th>
              </Tr>
            </THead>
            <TBody>
              {sites.map((s) => (
                <Tr key={`${s.id ?? ''}-${s.name}`}>
                  <Td>{s.name || '—'}</Td>
                  <Td><StatusBadge status={s.status} /></Td>
                  <Td muted>{s.region || '—'}</Td>
                  <Td muted>{s.tenant || '—'}</Td>
                  <Td numeric muted>{isNum(s.device_count) ? s.device_count : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      <Card as="section">
        <CardHeader title="NetBox" />
        <KVGrid>
          <KV label="Version" mono>{data.version}</KV>
          <KV label="Python" mono>{data.python_version}</KV>
          <KV label="Background workers">{isNum(data.workers_running) ? data.workers_running : null}</KV>
          <KV label="Collected">{formatDate(data.collected_at)}</KV>
        </KVGrid>
      </Card>
    </div>
  );
}
