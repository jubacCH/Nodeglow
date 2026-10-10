'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { EnabledPill, KV, KVGrid, KVList, KVRow, StatGrid, StatTile, TableCard, UsageBar, isNum, isSecretKey, uptime } from './parts';

interface SwisscomDevice {
  model: string;
  serial: string;
  firmware: string;
  mac: string;
  uptime_s: number;
  manufacturer: string;
  external_ip: string;
  hardware: string;
  status: string;
  reboots: number;
  first_use: string;
  mem_total_kb?: number;
  mem_free_kb?: number;
  mem_pct?: number;
}

interface SwisscomHost {
  name: string;
  mac: string;
  ip: string;
  active: boolean;
  device_type: string;
  first_seen: string;
  last_connection: string;
}

interface SwisscomData {
  device: SwisscomDevice;
  wan: Record<string, string>;
  wifi: { enabled?: boolean; scheduler?: boolean };
  hosts: SwisscomHost[];
  hosts_active: number;
  hosts_total: number;
}

function DeviceStatus({ status }: { status: string | null | undefined }) {
  if (!status) return <StatusPill status="unknown" />;
  return <StatusPill status={status === 'Up' ? 'ok' : 'down'}>{status}</StatusPill>;
}

function memDetail(d: SwisscomDevice): string | undefined {
  if (!isNum(d.mem_pct)) return undefined;
  if (!isNum(d.mem_total_kb) || !d.mem_total_kb) return `${d.mem_pct} %`;
  const used = Math.round((d.mem_total_kb - (d.mem_free_kb ?? 0)) / 1024);
  return `${d.mem_pct} % (${used} / ${Math.round(d.mem_total_kb / 1024)} MB)`;
}

export function SwisscomDetail({ data }: { data: SwisscomData }) {
  const d = data.device ?? ({} as SwisscomDevice);
  const hosts = data.hosts ?? [];
  const activeHosts = hosts.filter((h) => h.active);
  const inactiveHosts = hosts.filter((h) => !h.active);

  return (
    <div className="space-y-6">
      {/* Stats row */}
      <StatGrid>
        <StatTile label="Uptime" value={uptime(d.uptime_s)} />
        <StatTile label="External IP" value={d.external_ip ? <span className="font-mono text-h3">{d.external_ip}</span> : null} />
        <StatTile
          label={isNum(data.hosts_total) ? `Connected devices · ${data.hosts_total} total` : 'Connected devices'}
          value={data.hosts_active}
        />
        <StatTile label="WAN" value={data.wan?.interface ? <span className="text-h3">{data.wan.interface}</span> : null} />
      </StatGrid>

      {/* Device info + memory */}
      <Card as="section" className="space-y-4">
        <CardHeader title="Device info" className="mb-0" />
        <KVGrid>
          <KV label="Model">{d.model}</KV>
          <KV label="Firmware" mono>{d.firmware}</KV>
          <KV label="Hardware">{d.hardware}</KV>
          <KV label="Manufacturer">{d.manufacturer}</KV>
          <KV label="Serial" mono>{d.serial}</KV>
          <KV label="MAC" mono>{d.mac}</KV>
          <KV label="Status"><DeviceStatus status={d.status} /></KV>
          <KV label="Reboots">{isNum(d.reboots) ? d.reboots : null}</KV>
        </KVGrid>

        {d.mem_pct != null && <UsageBar label="Memory" pct={d.mem_pct} detail={memDetail(d)} />}
      </Card>

      {/* WAN + WiFi */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card as="section">
          <CardHeader title="WAN" />
          <KVList>
            {Object.entries(data.wan ?? {}).map(([k, v]) => (
              <KVRow key={k} label={k.replace(/_/g, ' ')} mono>
                {isSecretKey(k) ? (v ? '••••••••' : null) : v == null ? null : String(v)}
              </KVRow>
            ))}
          </KVList>
        </Card>
        <Card as="section">
          <CardHeader title="WiFi" />
          <KVList>
            <KVRow label="Status"><EnabledPill enabled={data.wifi?.enabled} /></KVRow>
            <KVRow label="Scheduler"><EnabledPill enabled={data.wifi?.scheduler} on="Active" off="Off" /></KVRow>
          </KVList>
        </Card>
      </div>

      {/* Connected devices */}
      <TableCard
        title="Connected devices"
        actions={
          <>
            <Badge tone="accent">{isNum(data.hosts_active) ? data.hosts_active : activeHosts.length} active</Badge>
            <Badge>{inactiveHosts.length} inactive</Badge>
          </>
        }
      >
        <Table>
          <THead>
            <Tr>
              <Th>Status</Th>
              <Th>Name</Th>
              <Th>IP</Th>
              <Th>MAC</Th>
              <Th>Type</Th>
            </Tr>
          </THead>
          <TBody>
            {[...activeHosts, ...inactiveHosts].map((host) => (
              <Tr key={host.mac}>
                <Td>
                  {/* Inactive = currently not connected; that is normal for client devices. */}
                  <StatusDot status={host.active ? 'ok' : 'disabled'} label={host.active ? 'Active' : 'Inactive'} />
                </Td>
                <Td className="max-w-[220px] truncate font-medium">{host.name || '—'}</Td>
                <Td muted className="whitespace-nowrap font-mono text-meta">{host.ip || '—'}</Td>
                <Td muted className="whitespace-nowrap font-mono text-meta">{host.mac}</Td>
                <Td muted className="text-meta">{host.device_type || '—'}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableCard>
    </div>
  );
}
