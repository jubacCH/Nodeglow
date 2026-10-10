'use client';

import type { ReactNode } from 'react';
import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { cn } from '@/lib/utils';
import type { HealthState } from '@/lib/status';
import { formatDateTime } from '../hostState';
import type { AgentMetrics, DeviceData, DiskInfo, DockerContainer, HostDetailData } from './types';

export function formatUptimeSeconds(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** Resource usage tone: neutral below 75 %, warning from 75 %, critical from 90 %. */
function usageTone(pct: number) {
  if (pct >= 90) return { bar: 'bg-down', text: 'text-down' };
  if (pct >= 75) return { bar: 'bg-warning', text: 'text-warning' };
  return { bar: 'bg-accent', text: 'text-fg' };
}

export function MetricTile({ label, value, pct, sub }: { label: string; value: string; pct?: number | null; sub?: ReactNode }) {
  const tone = pct != null ? usageTone(pct) : null;
  return (
    <Card padding="sm">
      <p className="text-meta text-fg-2">{label}</p>
      <p className={cn('num mt-1 font-display text-num-sm font-medium tracking-[-0.04em]', value === '—' ? 'text-fg-3' : tone?.text ?? 'text-fg')}>{value}</p>
      {pct != null && (
        <div
          className="mt-2 h-1.5 overflow-hidden rounded-pill bg-surface-3"
          role="meter"
          aria-label={`${label} usage`}
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className={cn('h-full rounded-pill', tone?.bar)} style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }} />
        </div>
      )}
      {sub && <p className="mt-1 text-meta text-fg-3">{sub}</p>}
    </Card>
  );
}

function fmtPct(v: number | null | undefined) {
  return v != null ? `${Number(v).toFixed(1)}%` : '—';
}

function DefinitionGrid({ items }: { items: [string, ReactNode, boolean?][] }) {
  const shown = items.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!shown.length) return <p className="text-ui text-fg-3">No details reported.</p>;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2 xl:grid-cols-4">
      {shown.map(([k, v, mono]) => (
        <div key={k} className="flex min-w-0 justify-between gap-3 border-b border-border pb-1.5">
          <dt className="shrink-0 text-meta text-fg-2">{k}</dt>
          <dd className={cn('min-w-0 truncate text-right text-ui text-fg', mono && 'font-mono text-meta')}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function containerDot(state?: string): HealthState {
  if (state === 'running') return 'ok';
  if (state === 'exited' || state === 'dead') return 'down';
  return 'warning';
}

function DockerCard({ containers }: { containers: DockerContainer[] }) {
  const running = containers.filter((c) => c.state === 'running').length;
  return (
    <Card as="section" aria-labelledby="host-docker-title" padding="none">
      <div className="px-5 pt-5 max-[759px]:px-4 max-[759px]:pt-4">
        <CardHeader title="Docker containers" titleId="host-docker-title" meta={`${running} of ${containers.length} running`} />
      </div>
      <TableContainer>
        <Table density="compact">
          <THead>
            <Tr>
              <Th>Container</Th>
              <Th className="max-[759px]:hidden">Image</Th>
              <Th>State</Th>
              <Th numeric>CPU</Th>
              <Th numeric>Memory</Th>
              <Th className="max-[759px]:hidden">Health</Th>
              <Th numeric className="max-[759px]:hidden">Restarts</Th>
            </Tr>
          </THead>
          <TBody>
            {containers.map((ct) => (
              <Tr key={ct.name}>
                <Td className="max-w-[220px]">
                  <span className="flex min-w-0 items-center gap-2">
                    <StatusDot status={containerDot(ct.state)} size="sm" glow={false} label="" />
                    <span className="truncate font-medium">{ct.name}</span>
                    {ct.update_available && <Badge tone="accent">update</Badge>}
                  </span>
                </Td>
                <Td muted className="max-w-[220px] truncate font-mono text-meta max-[759px]:hidden">{ct.image}</Td>
                <Td muted className="text-meta">{ct.status || ct.state || '—'}</Td>
                <Td numeric>{ct.cpu_pct != null ? `${ct.cpu_pct.toFixed(1)}%` : '—'}</Td>
                <Td numeric>{ct.mem_mb != null ? (ct.mem_mb >= 1024 ? `${(ct.mem_mb / 1024).toFixed(1)} GB` : `${Math.round(ct.mem_mb)} MB`) : '—'}</Td>
                <Td className="text-meta max-[759px]:hidden">
                  {ct.health === 'healthy' ? <span className="text-ok">healthy</span>
                    : ct.health === 'unhealthy' ? <span className="text-down">unhealthy</span>
                      : <span className="text-fg-3">—</span>}
                </Td>
                <Td numeric className={cn('max-[759px]:hidden', (ct.restart_count ?? 0) > 0 ? 'text-warning' : 'text-fg-3')}>{ct.restart_count ?? 0}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableContainer>
    </Card>
  );
}

function DisksCard({ disks }: { disks: DiskInfo[] }) {
  return (
    <Card as="section" aria-labelledby="host-disks-title">
      <CardHeader title="Disks" titleId="host-disks-title" />
      <ul className="space-y-3">
        {disks.map((d) => {
          const tone = usageTone(d.pct ?? 0);
          return (
            <li key={d.mount}>
              <div className="mb-1 flex items-center justify-between gap-3 text-meta">
                <span className="truncate font-mono text-fg-2">{d.mount}</span>
                <span className="num shrink-0 text-fg-2">
                  {d.used_gb?.toFixed(1)} / {d.total_gb?.toFixed(1)} GB
                  <span className={cn('ml-2', tone.text)}>{d.pct?.toFixed(1)}%</span>
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-pill bg-surface-3" role="meter" aria-label={`${d.mount} usage`} aria-valuenow={Math.round(d.pct ?? 0)} aria-valuemin={0} aria-valuemax={100}>
                <div className={cn('h-full rounded-pill', tone.bar)} style={{ width: `${Math.min(d.pct ?? 0, 100)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Agent-reported metrics, containers, disks and agent info. */
export function AgentSection({ agent, stale }: { agent: AgentMetrics; stale: boolean }) {
  const containers = agent.extra?.docker_containers ?? [];
  const disks = agent.extra?.disks ?? [];
  return (
    <section aria-labelledby="host-system-title" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="host-system-title" className="text-body font-medium text-fg">System metrics</h2>
        <span className={cn('text-meta', stale ? 'text-unknown' : 'text-fg-3')}>
          {agent.snapshot_time ? `Agent snapshot ${formatDateTime(agent.snapshot_time)}` : 'No snapshot yet'}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <MetricTile label="CPU" value={fmtPct(agent.cpu_pct)} pct={agent.cpu_pct} />
        <MetricTile
          label="Memory"
          value={fmtPct(agent.mem_pct)}
          pct={agent.mem_pct}
          sub={agent.mem_used_mb != null && agent.mem_total_mb != null ? `${(agent.mem_used_mb / 1024).toFixed(1)} / ${(agent.mem_total_mb / 1024).toFixed(1)} GB` : undefined}
        />
        <MetricTile label="Disk" value={fmtPct(agent.disk_pct)} pct={agent.disk_pct} />
        <MetricTile label="System uptime" value={formatUptimeSeconds(agent.uptime_s)} />
        {agent.load_1 != null && (
          <MetricTile
            label="Load average"
            value={agent.load_1.toFixed(2)}
            sub={[agent.load_1, agent.load_5, agent.load_15].filter((v): v is number => v != null).map((v) => v.toFixed(2)).join(' / ')}
          />
        )}
        {(agent.rx_bytes != null || agent.tx_bytes != null) && (
          <MetricTile label="Network received" value={formatBytes(agent.rx_bytes)} sub={`Sent ${formatBytes(agent.tx_bytes)}`} />
        )}
      </div>
      {containers.length > 0 && <DockerCard containers={containers} />}
      {disks.length > 0 && <DisksCard disks={disks} />}
      <Card as="section" aria-labelledby="host-agent-title">
        <CardHeader title="Agent" titleId="host-agent-title" />
        <DefinitionGrid
          items={[
            ['Name', agent.agent_name],
            ['Platform', agent.platform || '—'],
            ['Architecture', agent.arch || '—'],
            ['Version', agent.agent_version || '—', true],
            ['Last seen', agent.last_seen ? formatDateTime(agent.last_seen) : '—'],
          ]}
        />
      </Card>
    </section>
  );
}

/** Metrics and details an integration (UniFi, Proxmox …) reports for this device. */
export function DeviceSection({ host, device }: { host: HostDetailData; device: DeviceData | null }) {
  const integ = host.integration;
  if (!integ) return null;
  const memPct = device?.mem_pct ?? (device?.mem_total_gb ? (device.mem_used_gb / device.mem_total_gb) * 100 : null);
  const raw = (() => {
    let data = integ.data;
    if (typeof data === 'string') { try { data = JSON.parse(data); } catch { /* keep */ } }
    if (!data || typeof data !== 'object') return [];
    return Object.entries(data as Record<string, unknown>).filter(([, v]) => v != null && typeof v !== 'object');
  })();
  return (
    <section aria-labelledby="host-device-title" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="host-device-title" className="flex items-center gap-2 text-body font-medium text-fg">
          Device · {integ.config_name}
          <Badge>{integ.type}</Badge>
          {(device?.type_label || device?.type) && <Badge>{device?.type_label || device?.type}</Badge>}
        </h2>
        <span className={cn('text-meta', integ.ok ? 'text-fg-3' : 'text-warning')}>
          {integ.ok ? '' : 'Last poll failed · '}Snapshot {formatDateTime(integ.timestamp)}
        </span>
      </div>
      {device && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {device.cpu_pct != null && <MetricTile label="CPU" value={fmtPct(device.cpu_pct)} pct={device.cpu_pct} />}
          {memPct != null && (
            <MetricTile
              label="Memory"
              value={fmtPct(memPct)}
              pct={memPct}
              sub={device.mem_used_gb != null && device.mem_total_gb != null ? `${device.mem_used_gb} / ${device.mem_total_gb} GB` : undefined}
            />
          )}
          {device.disk_pct != null && (
            <MetricTile
              label="Disk"
              value={fmtPct(device.disk_pct)}
              pct={device.disk_pct}
              sub={device.disk_used_gb != null && device.disk_total_gb != null ? `${device.disk_used_gb} / ${device.disk_total_gb} GB` : undefined}
            />
          )}
          {device.uptime_s > 0 && <MetricTile label="Device uptime" value={formatUptimeSeconds(device.uptime_s)} />}
          {(device.clients_wifi != null || device.clients_wired != null) && (
            <MetricTile
              label="Clients"
              value={String((device.clients_wifi ?? 0) + (device.clients_wired ?? 0))}
              sub={`Wi-Fi ${device.clients_wifi ?? 0} · wired ${device.clients_wired ?? 0}`}
            />
          )}
          {(device.netin != null || device.rx_bytes != null) && (
            <MetricTile label="Traffic received" value={formatBytes(device.rx_bytes ?? device.netin)} sub={`Sent ${formatBytes(device.tx_bytes ?? device.netout)}`} />
          )}
          {device.satisfaction != null && device.satisfaction >= 0 && (
            <MetricTile label="Satisfaction" value={`${device.satisfaction}%`} />
          )}
        </div>
      )}
      <Card>
        <DefinitionGrid
          items={device
            ? [
                ['IP', device.ip, true],
                ['MAC', device.mac, true],
                ['Firmware', device.version, true],
                ['Model', device.model],
                ['Node', device.node],
                ['VMID', device.id != null ? String(device.id) : null, true],
                ['Status', device.status],
              ]
            : raw.map(([k, v]) => [k.replace(/_/g, ' '), String(v), true] as [string, ReactNode, boolean])}
        />
      </Card>
    </section>
  );
}

/** Static facts about the host. */
export function HostFactsCard({ host, checkedBy, className }: { host: HostDetailData; checkedBy: string; className?: string }) {
  return (
    <Card as="section" aria-labelledby="host-facts-title" className={className}>
      <CardHeader title="Details" titleId="host-facts-title" />
      <dl className="space-y-2">
        {([
          ['Address', host.hostname, true],
          ['Port', host.port ? String(host.port) : null, true],
          ['Checks', (host.check_type || 'icmp').split(',').join(', '), true],
          ['Checked by', checkedBy],
          ['Latency threshold', host.latency_threshold_ms ? `${host.latency_threshold_ms} ms` : 'Global default'],
          ['Source', host.source_detail ? `${host.source} · ${host.source_detail}` : host.source],
          ['MAC', host.mac_address, true],
          ['TLS certificate', host.ssl_expiry_days != null ? `expires in ${host.ssl_expiry_days} days` : null],
          ['Added', host.created_at ? new Date(host.created_at).toLocaleDateString() : null],
        ] as [string, string | null, boolean?][]).filter(([, v]) => v).map(([k, v, mono]) => (
          <div key={k} className="flex min-w-0 justify-between gap-3 border-b border-border pb-1.5 last:border-b-0">
            <dt className="shrink-0 text-meta text-fg-2">{k}</dt>
            <dd
              className={cn(
                'min-w-0 truncate text-right text-ui',
                mono && 'font-mono text-meta',
                k === 'TLS certificate' && host.ssl_expiry_days != null && host.ssl_expiry_days <= 14 ? 'text-down'
                  : k === 'TLS certificate' && host.ssl_expiry_days != null && host.ssl_expiry_days <= 30 ? 'text-warning' : 'text-fg',
              )}
            >
              {v}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
