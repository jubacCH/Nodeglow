'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, StatGrid, StatTile, StateLabel, TableCard, UsageBar, isNum, uptime } from './parts';

interface FirewallInterface {
  name?: string;
  status?: string;
  ipaddr?: string;
  media?: string;
  [key: string]: unknown;
}

interface FirewallData {
  fw_type: string;
  version: string;
  hostname: string;
  cpu_pct: number;
  mem_pct: number;
  uptime_s: number;
  interfaces: FirewallInterface[];
  alerts: number;
}

function ifaceState(status: string | undefined): HealthState {
  const s = status?.toLowerCase();
  if (s === 'up') return 'ok';
  if (s === 'down' || s === 'no carrier') return 'down';
  return 'unknown';
}

export function FirewallDetail({ data }: { data: FirewallData }) {
  const alerts = isNum(data.alerts) ? data.alerts : null;
  return (
    <div className="space-y-6">
      {/* Stats */}
      <StatGrid>
        <StatTile label="Type" value={data.fw_type ? data.fw_type.toUpperCase() : null} />
        <StatTile label="Uptime" value={uptime(data.uptime_s)} />
        <StatTile label="Interfaces" value={data.interfaces ? data.interfaces.length : null} />
        <StatTile label="Alerts" value={alerts} state={alerts !== null && alerts > 0 ? 'warning' : undefined} />
      </StatGrid>

      {/* System info + metrics */}
      <Card as="section" className="space-y-4">
        <CardHeader title="System" className="mb-0" />
        <KVGrid className="md:grid-cols-3">
          <KV label="Hostname" mono>{data.hostname}</KV>
          <KV label="Version" mono>{data.version}</KV>
          <KV label="Firewall">{data.fw_type}</KV>
        </KVGrid>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <UsageBar label="CPU" pct={data.cpu_pct} />
          <UsageBar label="Memory" pct={data.mem_pct} />
        </div>
      </Card>

      {/* Interfaces */}
      {data.interfaces && data.interfaces.length > 0 && (
        <TableCard title="Interfaces" meta={`${data.interfaces.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Name</Th>
                <Th>IP address</Th>
                <Th>Media</Th>
              </Tr>
            </THead>
            <TBody>
              {data.interfaces.map((iface, i) => (
                <Tr key={iface.name ?? i}>
                  <Td className="whitespace-nowrap">
                    <StateLabel status={ifaceState(iface.status)}>{iface.status ?? 'No data'}</StateLabel>
                  </Td>
                  <Td className="font-mono">{iface.name ?? '—'}</Td>
                  <Td muted className="font-mono">{iface.ipaddr || '—'}</Td>
                  <Td muted>{iface.media || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
