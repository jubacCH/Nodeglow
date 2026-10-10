'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { KV, KVGrid, SectionTitle, StatGrid, StatTile, TableCard, UsageBar, fixed, isNum, tempClass, uptime } from './parts';

interface TruenasSystem {
  hostname: string;
  version: string;
  uptime_s: number;
  platform: string;
  model: string;
}

interface TruenasPool {
  name: string;
  status: string;
  healthy: boolean;
  size_gb: number;
  used_gb: number;
  free_gb: number;
  pct: number;
}

interface TruenasDisk {
  name: string;
  serial: string;
  model: string;
  size_gb: number;
  temp: number | null;
  type: string;
}

interface TruenasAlert {
  level: string;
  message: string;
  date: string;
}

interface TruenasTotals {
  pools_total: number;
  pools_healthy: number;
  disks_total: number;
  storage_used_gb: number;
  storage_total_gb: number;
  storage_pct: number;
}

interface TruenasData {
  system: TruenasSystem;
  storage_pools: TruenasPool[];
  disks: TruenasDisk[];
  alerts: TruenasAlert[];
  totals: TruenasTotals;
}

function boolState(v: boolean | null | undefined): HealthState {
  return v === true ? 'ok' : v === false ? 'down' : 'unknown';
}

function alertTone(level: string | undefined): 'down' | 'warning' | 'neutral' {
  if (level === 'CRITICAL') return 'down';
  if (level === 'WARNING') return 'warning';
  return 'neutral';
}

export function TruenasDetail({ data }: { data: TruenasData }) {
  const { storage_pools, disks, alerts } = data;
  const system = data.system ?? ({} as TruenasSystem);
  const totals = data.totals ?? ({} as TruenasTotals);
  const poolsHealthy = isNum(totals.pools_healthy) && isNum(totals.pools_total) ? `${totals.pools_healthy}/${totals.pools_total}` : null;

  return (
    <div className="space-y-6">
      {/* System info */}
      <Card as="section">
        <CardHeader title="System" />
        <KVGrid className="md:grid-cols-3 lg:grid-cols-5">
          <KV label="Hostname" mono>{system.hostname}</KV>
          <KV label="Version" mono>{system.version}</KV>
          <KV label="Platform">{system.platform}</KV>
          <KV label="Model">{system.model}</KV>
          <KV label="Uptime">{uptime(system.uptime_s)}</KV>
        </KVGrid>
      </Card>

      {/* Stat tiles */}
      <StatGrid>
        <StatTile
          label="Pools healthy"
          value={poolsHealthy}
          state={isNum(totals.pools_healthy) && isNum(totals.pools_total) && totals.pools_healthy < totals.pools_total ? 'down' : undefined}
        />
        <StatTile label="Disks" value={totals.disks_total} />
        <StatTile label="Used" value={fixed(totals.storage_used_gb)} unit="GB" />
        <StatTile label="Storage" value={fixed(totals.storage_pct)} unit="%" />
      </StatGrid>

      {/* Storage pools */}
      {storage_pools && storage_pools.length > 0 && (
        <section>
          <SectionTitle>Storage pools</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {storage_pools.map((pool) => (
              <Card key={pool.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{pool.name}</span>
                  <StatusPill status={boolState(pool.healthy)}>{pool.status || undefined}</StatusPill>
                </div>
                <UsageBar
                  label="Usage"
                  pct={pool.pct}
                  detail={isNum(pool.used_gb) && isNum(pool.size_gb) && isNum(pool.pct)
                    ? `${pool.used_gb.toFixed(1)} / ${pool.size_gb.toFixed(1)} GB (${pool.pct.toFixed(1)} %)`
                    : undefined}
                />
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Disks table */}
      {disks && disks.length > 0 && (
        <TableCard title="Disks" meta={`${disks.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Model</Th>
                <Th>Serial</Th>
                <Th>Type</Th>
                <Th numeric>Size</Th>
                <Th numeric>Temp</Th>
              </Tr>
            </THead>
            <TBody>
              {disks.map((d) => (
                <Tr key={d.name}>
                  <Td className="font-mono">{d.name}</Td>
                  <Td muted className="whitespace-nowrap">{d.model || '—'}</Td>
                  <Td muted className="whitespace-nowrap font-mono text-meta">{d.serial || '—'}</Td>
                  <Td>{d.type ? <Badge>{d.type}</Badge> : '—'}</Td>
                  <Td numeric className="whitespace-nowrap">{isNum(d.size_gb) ? `${d.size_gb.toFixed(1)} GB` : '—'}</Td>
                  <Td numeric className={cn('whitespace-nowrap', tempClass(d.temp, 40, 50))}>
                    {isNum(d.temp) ? `${d.temp} °C` : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Alerts */}
      {alerts && alerts.length > 0 && (
        <Card as="section">
          <CardHeader title="Alerts" meta={`${alerts.length}`} />
          <ul className="space-y-3">
            {alerts.map((a, i) => (
              <li key={i} className="flex min-w-0 items-start gap-3 text-ui">
                <Badge tone={alertTone(a.level)} className="shrink-0">{a.level || '—'}</Badge>
                <div className="min-w-0 flex-1">
                  <p className="break-words text-fg">{a.message}</p>
                  <p className="mt-0.5 text-meta text-fg-3">{a.date}</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
