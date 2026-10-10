'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { KV, KVGrid, StatGrid, StatTile, TableCard, isNum } from './parts';

interface RedfishTemp {
  name: string;
  reading_c: number;
  status: string;
  threshold_c: number | null;
}

interface RedfishFan {
  name: string;
  rpm: number | null;
  status: string;
}

interface RedfishData {
  hostname: string;
  manufacturer: string;
  model: string;
  serial: string;
  bios_version: string;
  status: string;
  healthy: boolean;
  power_state: string;
  cpu_count: number;
  memory_gb: number;
  temperatures: RedfishTemp[];
  fans: RedfishFan[];
  power_watts: number | null;
  health_summary: string;
}

/** Redfish health: OK / Warning / Critical; anything else is no data. */
function redfishState(status: string | null | undefined): HealthState {
  switch (status?.toLowerCase()) {
    case 'ok': return 'ok';
    case 'warning': return 'warning';
    case 'critical': return 'down';
    default: return 'unknown';
  }
}

function tempClass(temp: unknown, threshold: number | null): string {
  if (!isNum(temp)) return 'text-fg-3';
  if (threshold && temp >= threshold) return 'text-down';
  if (temp >= 80) return 'text-down';
  if (temp >= 60) return 'text-warning';
  return 'text-fg';
}

function PowerState({ state }: { state: string | null | undefined }) {
  if (state === 'On') return <StatusPill status="ok">Power on</StatusPill>;
  if (!state) return <StatusPill status="unknown">Power: no data</StatusPill>;
  // Off (or transitional states) is a neutral fact, not a health state.
  return <Badge>Power {state.toLowerCase()}</Badge>;
}

export function RedfishDetail({ data }: { data: RedfishData }) {
  const health: HealthState = data.healthy === true ? 'ok' : data.healthy === false ? 'down' : 'unknown';
  return (
    <div className="space-y-6">
      {/* Health banner */}
      <Card padding="sm" glow={health === 'down' ? 'crit' : undefined}>
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <StatusDot status={health} size="lg" />
          <div className="min-w-0">
            <p className="truncate font-mono text-ui font-medium text-fg">{data.hostname || '—'}</p>
            <p className="truncate text-meta text-fg-3">{[data.manufacturer, data.model].filter(Boolean).join(' ') || '—'}</p>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <PowerState state={data.power_state} />
            <StatusPill status={health}>{data.health_summary || undefined}</StatusPill>
          </div>
        </div>
      </Card>

      {/* Stats */}
      <StatGrid>
        <StatTile label="CPUs" value={data.cpu_count} />
        <StatTile label="Memory" value={isNum(data.memory_gb) ? data.memory_gb : null} unit="GB" />
        <StatTile label="Power" value={isNum(data.power_watts) ? data.power_watts : null} unit="W" />
        <StatTile label="Fans" value={data.fans ? data.fans.length : null} />
      </StatGrid>

      {/* Device info */}
      <Card as="section">
        <CardHeader title="Hardware" />
        <KVGrid>
          <KV label="Manufacturer">{data.manufacturer}</KV>
          <KV label="Model">{data.model}</KV>
          <KV label="Serial" mono>{data.serial}</KV>
          <KV label="BIOS" mono>{data.bios_version}</KV>
        </KVGrid>
      </Card>

      {/* Temperatures */}
      {data.temperatures && data.temperatures.length > 0 && (
        <TableCard title="Temperatures" meta={`${data.temperatures.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Sensor</Th>
                <Th numeric>Reading</Th>
                <Th numeric>Threshold</Th>
              </Tr>
            </THead>
            <TBody>
              {data.temperatures.map((t) => (
                <Tr key={t.name}>
                  <Td><StatusPill size="sm" status={redfishState(t.status)}>{t.status || undefined}</StatusPill></Td>
                  <Td>{t.name}</Td>
                  <Td numeric className={cn('whitespace-nowrap', tempClass(t.reading_c, t.threshold_c))}>
                    {isNum(t.reading_c) ? `${t.reading_c} °C` : '—'}
                  </Td>
                  <Td numeric muted className="whitespace-nowrap">
                    {isNum(t.threshold_c) ? `${t.threshold_c} °C` : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Fans */}
      {data.fans && data.fans.length > 0 && (
        <TableCard title="Fans" meta={`${data.fans.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>Fan</Th>
                <Th numeric>RPM</Th>
              </Tr>
            </THead>
            <TBody>
              {data.fans.map((f) => (
                <Tr key={f.name}>
                  <Td><StatusPill size="sm" status={redfishState(f.status)}>{f.status || undefined}</StatusPill></Td>
                  <Td>{f.name}</Td>
                  <Td numeric muted>{isNum(f.rpm) ? f.rpm : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
