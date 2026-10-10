'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { KV, KVGrid, SectionTitle, UsageBar, isNum, tempClass, uptime } from './parts';

interface SynologySystem {
  model: string;
  dsm_version: string;
  uptime_s: number;
  mem_pct: number;
  cpu_pct: number;
  temp_c: number;
}

interface SynologyPool {
  name: string;
  status: string;
  used_pct: number;
  used_human: string;
  total_human: string;
}

interface SynologyData {
  system: SynologySystem;
  storage_pools: SynologyPool[];
}

function poolState(status: string | null | undefined): HealthState {
  if (!status) return 'unknown';
  if (status === 'normal' || status === 'healthy') return 'ok';
  if (status === 'degraded') return 'degraded';
  return 'down';
}

export function SynologyDetail({ data }: { data: SynologyData }) {
  const { storage_pools } = data;
  const system = data.system ?? ({} as SynologySystem);

  return (
    <div className="space-y-6">
      {/* System info */}
      <Card as="section">
        <CardHeader title="System information" />
        <KVGrid>
          <KV label="Model">{system.model}</KV>
          <KV label="DSM version" mono>{system.dsm_version}</KV>
          <KV label="Uptime">{uptime(system.uptime_s)}</KV>
          <KV label="Temperature">
            {isNum(system.temp_c) ? <span className={cn('num', tempClass(system.temp_c, 60, 75))}>{system.temp_c} °C</span> : null}
          </KV>
        </KVGrid>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <UsageBar label="CPU" pct={system.cpu_pct} />
          <UsageBar label="Memory" pct={system.mem_pct} />
        </div>
      </Card>

      {/* Storage pools */}
      {storage_pools && storage_pools.length > 0 && (
        <section>
          <SectionTitle>Storage pools</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {storage_pools.map((pool) => (
              <Card key={pool.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{pool.name}</span>
                  <StatusPill status={poolState(pool.status)}>{pool.status || undefined}</StatusPill>
                </div>
                <UsageBar
                  label="Usage"
                  pct={pool.used_pct}
                  detail={pool.used_human && pool.total_human ? `${pool.used_human} / ${pool.total_human}` : undefined}
                />
              </Card>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
