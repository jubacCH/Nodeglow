'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import {
  KV,
  KVGrid,
  StatGrid,
  StatTile,
  StateLabel,
  TableCard,
  UsageBar,
  formatBytes,
  formatCount,
  formatDate,
  isNum,
} from './parts';

interface FortigateInterface {
  name: string;
  link: boolean | null;
  speed_mbps: number | null;
  rx_bytes: number | null;
  tx_bytes: number | null;
  rx_errors: number | null;
  tx_errors: number | null;
  ipaddr: string;
  admin_status: string | null;
  type: string | null;
  role: string | null;
  alias: string | null;
}

interface FortigatePhase2 {
  name: string;
  status: string | null;
}

interface FortigateTunnel {
  name: string;
  remote_gateway: string;
  type: string;
  state: string;
  phase2: FortigatePhase2[];
}

interface FortigateHa {
  mode: string | null;
  state: string | null;
  peers: { hostname: string; serial: string; priority: number | null; primary: boolean | null }[] | null;
  peer_count: number | null;
  in_sync: boolean | null;
}

interface FortigateLicense {
  name: string;
  status: string | null;
  expires_at: string;
}

interface FortigateFirmware {
  current: string | null;
  available: string[];
  update_available: boolean;
}

interface FortigateData {
  collected_at?: string;
  hostname?: string;
  model?: string;
  version?: string;
  serial?: string;
  vdom?: string;
  cpu_pct?: number | null;
  mem_pct?: number | null;
  sessions?: number | null;
  session_rate?: number | null;
  interfaces?: FortigateInterface[] | null;
  ipsec?: FortigateTunnel[] | null;
  ha?: FortigateHa;
  licenses?: FortigateLicense[] | null;
  firmware?: FortigateFirmware | null;
  unavailable?: string[];
}

function linkState(i: FortigateInterface): HealthState | 'disabled' {
  if (i.admin_status === 'down') return 'disabled';
  if (i.link === true) return 'ok';
  if (i.link === false) return i.admin_status === 'up' ? 'warning' : 'unknown';
  return 'unknown';
}

function linkText(i: FortigateInterface): string {
  if (i.admin_status === 'down') return 'Disabled';
  if (i.link === true) return 'Up';
  if (i.link === false) return 'No link';
  return 'No data';
}

function tunnelState(state: string | undefined): HealthState {
  if (state === 'up') return 'ok';
  if (state === 'down') return 'down';
  return 'unknown';
}

function haState(state: string | null | undefined): HealthState {
  if (state === 'healthy' || state === 'standalone') return 'ok';
  if (state === 'out_of_sync') return 'warning';
  if (state === 'peer_missing') return 'down';
  return 'unknown';
}

const HA_TEXT: Record<string, string> = {
  healthy: 'Healthy',
  standalone: 'Standalone',
  out_of_sync: 'Out of sync',
  peer_missing: 'Peer missing',
};

/** Whole days between the snapshot and the expiry, or null when unparsable. */
function daysLeft(expires: string, collected: string | undefined): number | null {
  const e = new Date(expires).getTime();
  const c = collected ? new Date(collected).getTime() : NaN;
  if (Number.isNaN(e) || Number.isNaN(c)) return null;
  return Math.floor((e - c) / 86_400_000);
}

export function FortigateDetail({ data }: { data: FortigateData }) {
  const interfaces = data.interfaces ?? null;
  const ipsec = data.ipsec ?? null;
  const licenses = data.licenses ?? null;
  const ha = data.ha;
  const unavailable = data.unavailable ?? [];
  const tunnelsUp = ipsec ? ipsec.filter((t) => t.state === 'up').length : null;
  const tunnelsDown = ipsec ? ipsec.filter((t) => t.state === 'down').length : null;

  return (
    <div className="space-y-6">
      {unavailable.length > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Could not read: <span className="text-fg">{unavailable.join(', ')}</span>. These values show as unknown.
          </p>
        </Card>
      )}

      <StatGrid>
        <StatTile label="Sessions" value={formatCount(data.sessions)} />
        <StatTile label="New sessions / s" value={formatCount(data.session_rate)} />
        <StatTile
          label="IPsec tunnels up"
          value={ipsec ? `${tunnelsUp}/${ipsec.length}` : null}
          state={tunnelsDown !== null && tunnelsDown > 0 ? 'down' : undefined}
        />
        <StatTile label="Interfaces" value={interfaces ? interfaces.length : null} />
      </StatGrid>

      <Card as="section" className="space-y-4">
        <CardHeader title="System" className="mb-0" />
        <KVGrid className="md:grid-cols-3 lg:grid-cols-5">
          <KV label="Hostname" mono>{data.hostname}</KV>
          <KV label="Model">{data.model}</KV>
          <KV label="Version" mono>{data.version}</KV>
          <KV label="Serial" mono>{data.serial}</KV>
          <KV label="VDOM" mono>{data.vdom}</KV>
        </KVGrid>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <UsageBar label="CPU" pct={data.cpu_pct} />
          <UsageBar label="Memory" pct={data.mem_pct} />
        </div>
        {data.firmware && (
          <p className="text-ui text-fg-2">
            {data.firmware.update_available
              ? `Firmware available: ${data.firmware.available.join(', ')}`
              : 'No newer firmware offered'}
          </p>
        )}
      </Card>

      <Card as="section" className="space-y-3">
        <CardHeader title="High availability" className="mb-0" />
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-ui">
          <StateLabel status={haState(ha?.state)}>{(ha?.state && HA_TEXT[ha.state]) || 'No data'}</StateLabel>
          <span className="text-fg-3">Mode</span>
          <span className="text-fg">{ha?.mode ?? '—'}</span>
          <span className="text-fg-3">Peers</span>
          <span className="num text-fg">{isNum(ha?.peer_count) ? ha?.peer_count : '—'}</span>
          <span className="text-fg-3">Config in sync</span>
          <span className="text-fg">{ha?.in_sync === true ? 'Yes' : ha?.in_sync === false ? 'No' : '—'}</span>
        </div>
        {ha?.peers && ha.peers.length > 0 && (
          <ul className="space-y-1 text-ui text-fg-2">
            {ha.peers.map((p, i) => (
              <li key={p.serial || i} className="font-mono">
                {p.hostname || '—'} · {p.serial || '—'}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {ipsec && ipsec.length > 0 && (
        <TableCard title="IPsec tunnels" meta={`${ipsec.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Tunnel</Th>
                <Th>Remote gateway</Th>
                <Th>Phase 2</Th>
              </Tr>
            </THead>
            <TBody>
              {ipsec.map((t) => (
                <Tr key={t.name}>
                  <Td className="whitespace-nowrap">
                    <StateLabel status={tunnelState(t.state)}>{t.state || 'No data'}</StateLabel>
                  </Td>
                  <Td className="font-mono">{t.name}</Td>
                  <Td muted className="font-mono">{t.remote_gateway || '—'}</Td>
                  <Td muted>
                    {t.phase2.length === 0
                      ? '—'
                      : t.phase2.map((p) => `${p.name || '?'}: ${p.status ?? 'unknown'}`).join(', ')}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {interfaces && interfaces.length > 0 && (
        <TableCard title="Interfaces" meta={`${interfaces.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Link</Th>
                <Th>Name</Th>
                <Th>IP address</Th>
                <Th numeric>Speed</Th>
                <Th numeric>RX</Th>
                <Th numeric>TX</Th>
                <Th numeric>Errors</Th>
              </Tr>
            </THead>
            <TBody>
              {interfaces.map((i) => (
                <Tr key={i.name}>
                  <Td className="whitespace-nowrap">
                    <StateLabel status={linkState(i)}>{linkText(i)}</StateLabel>
                  </Td>
                  <Td className="font-mono">
                    {i.name}
                    {i.role ? <Badge className="ml-2">{i.role}</Badge> : null}
                  </Td>
                  <Td muted className="font-mono">{i.ipaddr || '—'}</Td>
                  <Td numeric className="whitespace-nowrap">
                    {isNum(i.speed_mbps) && i.speed_mbps > 0 ? `${i.speed_mbps} Mbit/s` : '—'}
                  </Td>
                  <Td numeric className="whitespace-nowrap">{formatBytes(i.rx_bytes) ?? '—'}</Td>
                  <Td numeric className="whitespace-nowrap">{formatBytes(i.tx_bytes) ?? '—'}</Td>
                  <Td numeric>
                    {isNum(i.rx_errors) || isNum(i.tx_errors) ? (i.rx_errors ?? 0) + (i.tx_errors ?? 0) : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {licenses && licenses.length > 0 && (
        <TableCard title="FortiGuard licences" meta={`${licenses.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Licence</Th>
                <Th numeric>Expires</Th>
                <Th numeric>Days left</Th>
              </Tr>
            </THead>
            <TBody>
              {licenses.map((l) => {
                const days = daysLeft(l.expires_at, data.collected_at);
                const state: HealthState = days === null ? 'unknown' : days < 0 ? 'down' : days <= 30 ? 'warning' : 'ok';
                return (
                  <Tr key={l.name}>
                    <Td className="whitespace-nowrap">
                      <StateLabel status={state}>{days === null ? 'No data' : days < 0 ? 'Expired' : 'Valid'}</StateLabel>
                    </Td>
                    <Td className="font-mono">{l.name}</Td>
                    <Td numeric className="whitespace-nowrap">{formatDate(l.expires_at, 'date') ?? '—'}</Td>
                    <Td numeric>{days === null ? '—' : days}</Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
