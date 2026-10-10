'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { KV, KVGrid, KVList, KVRow, SectionTitle, TableCard, UsageBar, isNum, tempClass, uptime } from './parts';

interface UnasSystem {
  hostname: string;
  version: string;
  uptime_s: number;
  cpu_pct: number;
  temp_c: number;
  mem_used_gb: number;
  mem_total_gb: number;
  mem_pct: number;
}

interface UnasDisk {
  name: string;
  model: string;
  size_gb: number;
  temp: number;
  status: string;
  status_label: string;
  ok: boolean;
  smart_ok: boolean;
  type: string;
  life_span?: number;
  power_on_hrs?: number;
}

interface UnasRaid {
  name: string;
  type_label: string;
  state: string;
  healthy: boolean;
  size_gb: number;
  used_gb: number;
  pct: number;
  active_devices: number;
  failed_devices: number;
}

interface UnasPool {
  name: string;
  size_gb: number;
  used_gb: number;
  free_gb: number;
  pct: number;
  healthy: boolean;
}

interface UnasTotals {
  disks_total: number;
  disks_ok: number;
  disks_error: number;
  disks_hot: number;
  raids_total: number;
  raids_healthy: number;
  pools_total: number;
  storage_used_gb: number;
  storage_total_gb: number;
  storage_pct: number;
}

interface UnasData {
  system: UnasSystem;
  disks: UnasDisk[];
  raids: UnasRaid[];
  storage_pools: UnasPool[];
  totals: UnasTotals;
}

function boolState(v: boolean | null | undefined): HealthState {
  return v === true ? 'ok' : v === false ? 'down' : 'unknown';
}

function gb(used: unknown, total: unknown, free?: unknown): string | undefined {
  if (!isNum(used) || !isNum(total)) return undefined;
  const base = `${used.toFixed(1)} / ${total.toFixed(1)} GB`;
  return isNum(free) ? `${base} (${free.toFixed(1)} GB free)` : base;
}

export function UnasDetail({ data }: { data: UnasData }) {
  const { disks, raids, storage_pools } = data;
  const system = data.system ?? ({} as UnasSystem);
  const totals = data.totals ?? ({} as UnasTotals);

  return (
    <div className="space-y-6">
      {/* System info */}
      <Card as="section">
        <CardHeader title="System information" />
        <KVGrid className="md:grid-cols-3 lg:grid-cols-5">
          <KV label="Hostname" mono>{system.hostname}</KV>
          <KV label="Version" mono>{system.version}</KV>
          <KV label="Uptime">{uptime(system.uptime_s)}</KV>
          <KV label="Temperature">
            {isNum(system.temp_c) ? <span className={cn('num', tempClass(system.temp_c, 60, 75))}>{system.temp_c} °C</span> : null}
          </KV>
          <KV label="Memory">{gb(system.mem_used_gb, system.mem_total_gb)}</KV>
        </KVGrid>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <UsageBar label="CPU" pct={system.cpu_pct} />
          <UsageBar label="Memory" pct={system.mem_pct} />
        </div>
      </Card>

      {/* Disk table */}
      {disks && disks.length > 0 && (
        <TableCard
          title="Disks"
          meta={isNum(totals.disks_ok) && isNum(totals.disks_total) ? `${totals.disks_ok}/${totals.disks_total} OK` : undefined}
          actions={
            <>
              {isNum(totals.disks_error) && totals.disks_error > 0 && <StatusPill status="down">{totals.disks_error} error</StatusPill>}
              {isNum(totals.disks_hot) && totals.disks_hot > 0 && <StatusPill status="warning">{totals.disks_hot} hot</StatusPill>}
            </>
          }
        >
          <Table>
            <THead>
              <Tr>
                <Th>Status</Th>
                <Th>SMART</Th>
                <Th>Name</Th>
                <Th>Model</Th>
                <Th>Type</Th>
                <Th numeric>Size</Th>
                <Th numeric>Temp</Th>
                <Th numeric>Power-on</Th>
              </Tr>
            </THead>
            <TBody>
              {disks.map((d) => (
                <Tr key={d.name}>
                  <Td><StatusPill size="sm" status={boolState(d.ok)}>{d.status_label || undefined}</StatusPill></Td>
                  <Td>
                    <StatusPill size="sm" status={boolState(d.smart_ok)}>
                      {d.smart_ok === true ? 'OK' : d.smart_ok === false ? 'Fail' : undefined}
                    </StatusPill>
                  </Td>
                  <Td className="whitespace-nowrap font-mono">{d.name}</Td>
                  <Td muted className="whitespace-nowrap">{d.model || '—'}</Td>
                  <Td>{d.type ? <Badge>{d.type}</Badge> : '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap">{isNum(d.size_gb) ? `${d.size_gb.toFixed(0)} GB` : '—'}</Td>
                  <Td numeric className={cn('whitespace-nowrap', tempClass(d.temp, 45, 55))}>{isNum(d.temp) ? `${d.temp} °C` : '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap">
                    {isNum(d.power_on_hrs) ? `${d.power_on_hrs.toLocaleString()} h` : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* RAID arrays */}
      {raids && raids.length > 0 && (
        <section>
          <SectionTitle meta={isNum(totals.raids_healthy) && isNum(totals.raids_total) ? `${totals.raids_healthy}/${totals.raids_total} healthy` : undefined}>
            RAID arrays
          </SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {raids.map((r) => (
              <Card key={r.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{r.name}</span>
                  <div className="flex items-center gap-2">
                    {r.type_label && <Badge>{r.type_label}</Badge>}
                    <StatusPill status={boolState(r.healthy)}>{r.state || undefined}</StatusPill>
                  </div>
                </div>
                <KVList>
                  <KVRow label="Active devices">{isNum(r.active_devices) ? r.active_devices : null}</KVRow>
                  <KVRow label="Failed devices">
                    {isNum(r.failed_devices) ? (
                      <span className={r.failed_devices > 0 ? 'text-down' : undefined}>{r.failed_devices}</span>
                    ) : null}
                  </KVRow>
                </KVList>
                <UsageBar label="Usage" pct={r.pct} detail={gb(r.used_gb, r.size_gb)} />
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Storage pools */}
      {storage_pools && storage_pools.length > 0 && (
        <section>
          <SectionTitle>Storage pools</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {storage_pools.map((p) => (
              <Card key={p.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{p.name}</span>
                  <StatusPill status={p.healthy === false ? 'degraded' : boolState(p.healthy)}>
                    {p.healthy === true ? 'Healthy' : p.healthy === false ? 'Degraded' : undefined}
                  </StatusPill>
                </div>
                <UsageBar label="Usage" pct={p.pct} detail={gb(p.used_gb, p.size_gb, p.free_gb)} />
              </Card>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
