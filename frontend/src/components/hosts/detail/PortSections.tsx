'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Lock, RefreshCw, Shield, X } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { useHostsV1 } from '@/hooks/queries/useHosts';
import { get, patch, post } from '@/lib/api';
import { cn, timeAgo } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import { MetricTile } from './MetricSections';
import type { ConnectedClient, HostDetailData, PortInfo } from './types';

function formatRate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null || bytesPerSec <= 0) return '—';
  const bits = bytesPerSec * 8;
  if (bits < 1000) return `${bits.toFixed(0)} bps`;
  if (bits < 1_000_000) return `${(bits / 1000).toFixed(1)} Kbps`;
  if (bits < 1_000_000_000) return `${(bits / 1_000_000).toFixed(1)} Mbps`;
  return `${(bits / 1_000_000_000).toFixed(2)} Gbps`;
}

function portLabel(p: PortInfo) {
  return !p.enable ? 'Disabled' : p.up ? 'Up' : 'Down';
}

/** Compact switch-port grid for the overview tab. */
export function PortSummaryCard({ ports, onShowAll }: { ports: PortInfo[]; onShowAll: () => void }) {
  const up = ports.filter((p) => p.up).length;
  const down = ports.filter((p) => p.enable && !p.up).length;
  const off = ports.filter((p) => !p.enable).length;
  return (
    <Card as="section" aria-labelledby="host-portsum-title">
      <CardHeader
        title="Switch ports"
        titleId="host-portsum-title"
        meta={`${up} up · ${down} down · ${off} disabled`}
        actions={<button type="button" onClick={onShowAll} className="text-accent hover:text-accent-hover">All ports</button>}
      />
      <ul className="flex flex-wrap gap-1.5" aria-label="Port states">
        {ports.map((p) => (
          <li
            key={p.idx}
            title={`Port ${p.idx}: ${portLabel(p)}${p.speed_label ? ` (${p.speed_label})` : ''}${p.is_uplink ? ', uplink' : ''}${p.poe_enable ? `, PoE ${p.poe_power} W` : ''}`}
            aria-label={`Port ${p.idx}: ${portLabel(p)}`}
            className={cn(
              'num grid h-8 w-8 place-items-center rounded-ng-sm border text-micro',
              !p.enable ? 'border-dashed border-border-2 text-fg-3' : p.up ? 'border-ok/30 bg-ok-soft text-ok' : 'border-border-2 bg-surface-2 text-fg-2',
            )}
          >
            {p.idx}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Full switch-port table and connected clients. */
export function PortsTab({ ports, clients }: { ports: PortInfo[]; clients: ConnectedClient[] }) {
  const { data: allHosts } = useHostsV1();
  const hostByAddr = useMemo(() => {
    const m = new Map<string, number>();
    for (const h of allHosts ?? []) if (h.hostname) m.set(h.hostname.toLowerCase(), h.id);
    return m;
  }, [allHosts]);
  const clientsByPort = useMemo(() => {
    const m = new Map<number, number>();
    for (const c of clients) if (c.sw_port != null) m.set(c.sw_port, (m.get(c.sw_port) ?? 0) + 1);
    return m;
  }, [clients]);
  const up = ports.filter((p) => p.up).length;
  const poe = ports.filter((p) => p.poe_enable && p.up).reduce((s, p) => s + p.poe_power, 0);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricTile label="Ports up" value={`${up} / ${ports.length}`} />
        <MetricTile label="Connected clients" value={String(clients.length)} />
        {poe > 0 && <MetricTile label="PoE power" value={`${poe.toFixed(1)} W`} />}
        <MetricTile
          label="Traffic received"
          value={formatRate(ports.reduce((s, p) => s + (p.rx_bytes_r || 0), 0))}
          sub={`Sent ${formatRate(ports.reduce((s, p) => s + (p.tx_bytes_r || 0), 0))}`}
        />
      </div>
      <Card padding="none">
        <TableContainer>
          <Table density="compact" aria-label="Switch ports">
            <THead>
              <Tr>
                <Th>Port</Th><Th>State</Th><Th>Speed</Th><Th>PoE</Th>
                <Th numeric>Received</Th><Th numeric>Sent</Th><Th numeric>Clients</Th>
              </Tr>
            </THead>
            <TBody>
              {ports.map((p) => (
                <Tr key={p.idx}>
                  <Td>
                    <span className="flex items-center gap-2">
                      <StatusDot status={!p.enable ? 'disabled' : p.up ? 'ok' : 'unknown'} size="sm" glow={false} label="" />
                      <span className="font-medium">{p.name}</span>
                      {p.is_uplink && <Badge tone="accent">Uplink</Badge>}
                    </span>
                  </Td>
                  <Td muted className="text-meta">{portLabel(p)}</Td>
                  <Td muted className="font-mono text-meta">{p.up && p.speed_label ? p.speed_label : '—'}</Td>
                  <Td muted className="text-meta">{p.poe_enable ? (p.poe_power > 0 ? `${p.poe_power} W` : 'On') : '—'}</Td>
                  <Td numeric>{p.up ? formatRate(p.rx_bytes_r) : '—'}</Td>
                  <Td numeric>{p.up ? formatRate(p.tx_bytes_r) : '—'}</Td>
                  <Td numeric>{clientsByPort.get(p.idx) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableContainer>
      </Card>
      {clients.length > 0 && (
        <Card as="section" aria-labelledby="host-clients-title" padding="none">
          <div className="px-5 pt-5 max-[759px]:px-4 max-[759px]:pt-4">
            <CardHeader title="Connected clients" titleId="host-clients-title" meta={String(clients.length)} />
          </div>
          <TableContainer>
            <Table density="compact">
              <THead>
                <Tr><Th>Client</Th><Th>IP</Th><Th>Port</Th><Th>Type</Th><Th>VLAN</Th><Th numeric>Traffic</Th></Tr>
              </THead>
              <TBody>
                {clients.map((c) => {
                  const hid = c.ip ? hostByAddr.get(c.ip.toLowerCase()) : undefined;
                  return (
                    <Tr key={c.mac}>
                      <Td>
                        {hid ? (
                          <Link prefetch={false} href={`/hosts/${hid}`} className="text-accent hover:text-accent-hover">{c.hostname || c.mac}</Link>
                        ) : (
                          <span>{c.hostname || '—'}</span>
                        )}
                        <span className="block font-mono text-micro text-fg-3">{c.mac}</span>
                      </Td>
                      <Td muted className="font-mono text-meta">{c.ip || '—'}</Td>
                      <Td muted>{c.sw_port ?? '—'}</Td>
                      <Td muted className="text-meta">{c.is_wireless ? `Wi-Fi${c.ssid ? ` (${c.ssid})` : ''}` : 'Wired'}</Td>
                      <Td muted>{c.vlan || '—'}</Td>
                      <Td numeric className="text-meta">↓{formatRate(c.rx_bytes_r)} ↑{formatRate(c.tx_bytes_r)}</Td>
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
          </TableContainer>
        </Card>
      )}
    </div>
  );
}

interface DiscoveredPort {
  id: number;
  port: number;
  protocol: string;
  service: string | null;
  status: string;
  has_ssl: boolean;
  ssl_issuer: string | null;
  ssl_subject: string | null;
  ssl_expiry_days: number | null;
  ssl_status: string;
  first_seen: string | null;
  last_seen: string | null;
  last_open: boolean;
}

const ORDER: Record<string, number> = { new: 0, monitored: 1, dismissed: 2 };

function MonitoredResult({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === false) return <span className="inline-flex items-center gap-1 text-meta text-down"><X size={12} aria-hidden="true" /> {label} failed</span>;
  if (ok === true) return <span className="inline-flex items-center gap-1 text-meta text-ok"><Check size={12} aria-hidden="true" /> {label} OK</span>;
  return <span className="text-meta text-fg-3">{label} monitored</span>;
}

/** Open ports found by the scanner, with monitor/dismiss actions. */
export function DiscoveredPortsCard({ host }: { host: HostDetailData }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const query = useQuery({
    queryKey: ['discovered-ports', host.id],
    queryFn: () => get<DiscoveredPort[]>(`/hosts/api/${host.id}/discovered-ports`),
  });
  const scan = useMutation({
    mutationFn: () => post(`/hosts/api/${host.id}/scan-ports`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['discovered-ports', host.id] }); toast('Port scan complete', 'success'); },
    onError: () => toast('Port scan failed', 'error'),
  });
  const act = useMutation({
    mutationFn: ({ portId, action }: { portId: number; action: string }) =>
      patch(`/hosts/api/${host.id}/discovered-ports/${portId}`, { action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['discovered-ports', host.id] });
      qc.invalidateQueries({ queryKey: ['host', host.id] });
    },
    onError: () => toast('Action failed', 'error'),
  });
  const detail = host.check_detail ?? {};
  const open = (query.data ?? []).filter((p) => p.last_open).sort((a, b) => {
    const ao = Math.min(ORDER[a.status] ?? 0, ORDER[a.ssl_status] ?? 0);
    const bo = Math.min(ORDER[b.status] ?? 0, ORDER[b.ssl_status] ?? 0);
    return ao - bo || a.port - b.port;
  });

  return (
    <Card as="section" aria-labelledby="host-dports-title">
      <CardHeader
        title="Discovered ports"
        titleId="host-dports-title"
        meta={open.length ? `${open.length} open` : undefined}
        actions={
          <Button size="sm" variant="ghost" onClick={() => scan.mutate()} loading={scan.isPending}>
            {!scan.isPending && <RefreshCw size={13} aria-hidden="true" />} {scan.isPending ? 'Scanning…' : 'Scan now'}
          </Button>
        }
      />
      <QueryState
        query={query}
        compact
        isEmpty={() => open.length === 0}
        empty={<EmptyState compact title="No open ports found" description="Run a scan to discover open ports on this host." />}
      >
        {() => (
          <ul className="space-y-2">
            {open.map((dp) => {
              const isNew = dp.status === 'new' || (dp.has_ssl && dp.ssl_status === 'new');
              const portOk = detail[`tcp:${dp.port}`] ?? detail.tcp ?? null;
              const httpsOk = detail.https ?? null;
              const sslTone = dp.ssl_expiry_days == null ? 'text-fg-3' : dp.ssl_expiry_days <= 14 ? 'text-down' : dp.ssl_expiry_days <= 30 ? 'text-warning' : 'text-fg-2';
              return (
                <li key={dp.id} className={cn('flex items-start gap-3 rounded-ctl border px-3 py-2.5', isNew ? 'border-accent/40 bg-accent-soft' : 'border-border bg-surface-2')}>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-ui font-semibold text-fg">{dp.port}</span>
                      <span className="text-meta text-fg-3">/{dp.protocol}</span>
                      {dp.service && <Badge>{dp.service}</Badge>}
                      {isNew && <Badge tone="accent">new</Badge>}
                      {dp.status === 'monitored' && <MonitoredResult ok={portOk} label="Port" />}
                      {dp.status === 'dismissed' && <span className="text-meta text-fg-3">dismissed</span>}
                    </div>
                    {dp.has_ssl && (
                      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-meta">
                        <Lock size={12} aria-hidden="true" className={sslTone} />
                        <span className="min-w-0 truncate text-fg-2">
                          {dp.ssl_subject}{dp.ssl_issuer && <span className="text-fg-3"> · {dp.ssl_issuer}</span>}
                        </span>
                        {dp.ssl_expiry_days != null && <span className={sslTone}>expires in {dp.ssl_expiry_days} d</span>}
                        {dp.ssl_status === 'monitored' && <MonitoredResult ok={httpsOk} label="HTTPS" />}
                      </div>
                    )}
                    <p className="mt-1 text-micro text-fg-3">first seen {timeAgo(dp.first_seen)} · last seen {timeAgo(dp.last_seen)}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {dp.status === 'new' && (
                      <>
                        <IconButton size="sm" variant="secondary" aria-label={`Monitor port ${dp.port}`} disabled={act.isPending} onClick={() => act.mutate({ portId: dp.id, action: 'monitor_port' })}>
                          <Check size={14} aria-hidden="true" />
                        </IconButton>
                        <IconButton size="sm" aria-label={`Dismiss port ${dp.port}`} disabled={act.isPending} onClick={() => act.mutate({ portId: dp.id, action: 'dismiss_port' })}>
                          <X size={14} aria-hidden="true" />
                        </IconButton>
                      </>
                    )}
                    {dp.status === 'monitored' && (
                      <IconButton
                        size="sm"
                        aria-label={`Stop monitoring port ${dp.port}`}
                        disabled={act.isPending}
                        onClick={async () => {
                          await act.mutateAsync({ portId: dp.id, action: 'unmonitor_port' });
                          if (dp.has_ssl && dp.ssl_status === 'monitored') act.mutate({ portId: dp.id, action: 'unmonitor_ssl' });
                        }}
                      >
                        <X size={14} aria-hidden="true" />
                      </IconButton>
                    )}
                    {dp.has_ssl && dp.ssl_status === 'new' && (
                      <>
                        <IconButton size="sm" variant="secondary" aria-label={`Monitor certificate on port ${dp.port}`} disabled={act.isPending} onClick={() => act.mutate({ portId: dp.id, action: 'monitor_ssl' })}>
                          <Shield size={14} aria-hidden="true" />
                        </IconButton>
                        <IconButton size="sm" aria-label={`Dismiss certificate on port ${dp.port}`} disabled={act.isPending} onClick={() => act.mutate({ portId: dp.id, action: 'dismiss_ssl' })}>
                          <X size={14} aria-hidden="true" />
                        </IconButton>
                      </>
                    )}
                    {dp.has_ssl && dp.ssl_status === 'monitored' && dp.status !== 'monitored' && (
                      <IconButton size="sm" aria-label={`Stop monitoring certificate on port ${dp.port}`} disabled={act.isPending} onClick={() => act.mutate({ portId: dp.id, action: 'unmonitor_ssl' })}>
                        <X size={14} aria-hidden="true" />
                      </IconButton>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

interface SyslogEntry {
  timestamp: string;
  severity: number;
  hostname: string;
  source_ip: string;
  app_name: string;
  message: string;
}

const SEV: Record<number, { label: string; tone: string }> = {
  0: { label: 'Emergency', tone: 'text-down' }, 1: { label: 'Alert', tone: 'text-down' },
  2: { label: 'Critical', tone: 'text-down' }, 3: { label: 'Error', tone: 'text-warning' },
  4: { label: 'Warning', tone: 'text-degraded' }, 5: { label: 'Notice', tone: 'text-fg-2' },
  6: { label: 'Info', tone: 'text-fg-2' }, 7: { label: 'Debug', tone: 'text-fg-3' },
};

/** Last 7 days of syslog from this host. */
export function HostSyslog({ hostId }: { hostId: number }) {
  const query = useQuery({
    queryKey: ['host-syslog', hostId],
    queryFn: () => get<SyslogEntry[]>(`/api/v1/syslog?host_id=${hostId}&limit=100&hours=168`),
    enabled: hostId > 0,
  });
  return (
    <Card padding="none">
      <QueryState
        query={query}
        empty={<EmptyState title="No syslog from this host" description="Nothing received in the last 7 days. Point the device's syslog at Nodeglow to see its messages here." />}
      >
        {(logs) => (
          <TableContainer>
            <Table density="compact" aria-label="Syslog messages">
              <THead>
                <Tr><Th>Time</Th><Th>Severity</Th><Th className="max-[759px]:hidden">App</Th><Th>Message</Th></Tr>
              </THead>
              <TBody>
                {logs.map((e, i) => (
                  <Tr key={`${e.timestamp}-${i}`}>
                    <Td muted className="whitespace-nowrap font-mono text-meta">{new Date(e.timestamp).toLocaleString()}</Td>
                    <Td className={cn('text-meta font-medium', SEV[e.severity]?.tone ?? 'text-fg-2')}>{SEV[e.severity]?.label ?? e.severity}</Td>
                    <Td muted className="font-mono text-meta max-[759px]:hidden">{e.app_name || '—'}</Td>
                    <Td className="max-w-[640px] truncate text-meta">{e.message}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableContainer>
        )}
      </QueryState>
    </Card>
  );
}
