'use client';

import { useId, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { Shield, ChevronDown, ChevronRight } from 'lucide-react';
import { BigNumber } from '@/components/ui/BigNumber';
import { SectionTitle, StatGrid, StatTile, formatBytes, formatCount, isNum } from './parts';

interface DnsRecord {
  type: string;
  name: string;
  content: string;
  proxied: boolean;
  ttl: number;
}

interface FirewallEvent {
  action: string;
  source: string;
  country: string;
  rule: string;
  host: string;
  uri: string;
  timestamp: string;
}

interface ZoneAnalytics {
  requests_all: number;
  requests_cached: number;
  cache_pct: number;
  bandwidth_all: number;
  bandwidth_cached: number;
  threats: number;
}

interface Zone {
  id: string;
  name: string;
  status: string;
  plan: string;
  ssl_mode: string;
  dns_records: DnsRecord[];
  dns_count: number;
  analytics: ZoneAnalytics;
  firewall_events: FirewallEvent[];
}

interface CloudflareData {
  zone_count: number;
  zones: Zone[];
  totals: {
    requests: number;
    bandwidth: number;
    threats: number;
    cache_pct: number;
  };
}

/** Cloudflare zone status → state. Pending zones are not live yet. */
function zoneState(status: string | null | undefined): HealthState {
  switch (status) {
    case 'active': return 'ok';
    case 'pending':
    case 'initializing': return 'degraded';
    case 'moved':
    case 'deleted':
    case 'deactivated': return 'down';
    default: return 'unknown';
  }
}

function pct(v: unknown): string | null {
  return isNum(v) ? `${v} %` : null;
}

function ZoneCard({ zone }: { zone: Zone }) {
  const [expanded, setExpanded] = useState(false);
  const [showDns, setShowDns] = useState(false);
  const bodyId = useId();
  const dnsId = useId();
  const a = zone.analytics;
  const threats = a?.threats;
  const records = zone.dns_records ?? [];
  const events = zone.firewall_events ?? [];

  return (
    <Card padding="sm">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full min-w-0 items-center gap-3 rounded-ctl text-left"
      >
        <StatusDot status={zoneState(zone.status)} label={zone.status ? `Zone ${zone.status}` : 'No data'} />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="truncate text-ui font-medium text-fg">{zone.name}</span>
            {zone.plan && <Badge>{zone.plan}</Badge>}
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-meta text-fg-3">
            <span className="num">{formatCount(a?.requests_all) ?? '—'} requests</span>
            <span className="num">{pct(a?.cache_pct) ?? '—'} cached</span>
            {isNum(threats) && threats > 0 && <span className="num text-warning">{threats} threats</span>}
            <span>SSL: {zone.ssl_mode || '—'}</span>
          </span>
        </span>
        <span className="num hidden shrink-0 text-meta text-fg-3 sm:inline">{isNum(zone.dns_count) ? zone.dns_count : '—'} records</span>
        {expanded ? <ChevronDown size={14} className="shrink-0 text-fg-3" aria-hidden="true" /> : <ChevronRight size={14} className="shrink-0 text-fg-3" aria-hidden="true" />}
      </button>

      {expanded && (
        <div id={bodyId} className="mt-4 space-y-4">
          {/* Analytics */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-ctl bg-surface-2 p-3"><BigNumber size="sm" label="Requests 24h" value={formatCount(a?.requests_all)} /></div>
            <div className="rounded-ctl bg-surface-2 p-3"><BigNumber size="sm" label="Cache hit" value={isNum(a?.cache_pct) ? a.cache_pct : null} unit="%" /></div>
            <div className="rounded-ctl bg-surface-2 p-3"><BigNumber size="sm" label="Bandwidth" value={formatBytes(a?.bandwidth_all)} /></div>
            <div className="rounded-ctl bg-surface-2 p-3">
              <BigNumber size="sm" label="Threats" value={formatCount(threats)} state={isNum(threats) && threats > 0 ? 'warning' : undefined} />
            </div>
          </div>

          {/* DNS records toggle */}
          <div>
            <button
              type="button"
              aria-expanded={showDns}
              aria-controls={dnsId}
              onClick={() => setShowDns(!showDns)}
              className="flex items-center gap-1 rounded-chip text-meta text-fg-2 transition-colors hover:text-fg"
            >
              {showDns ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
              DNS records ({isNum(zone.dns_count) ? zone.dns_count : records.length})
            </button>
            {showDns && (
              <div id={dnsId} className="mt-2">
                <TableContainer maxHeight={300}>
                  <Table density="compact">
                    <THead sticky>
                      <Tr>
                        <Th>Type</Th>
                        <Th>Name</Th>
                        <Th>Content</Th>
                        <Th>Proxy</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {records.map((r, i) => (
                        <Tr key={i}>
                          <Td><Badge>{r.type}</Badge></Td>
                          <Td className="max-w-[200px] truncate font-mono text-meta">{r.name}</Td>
                          <Td muted className="max-w-[200px] truncate font-mono text-meta">{r.content}</Td>
                          <Td>
                            {r.proxied ? <Badge tone="accent">Proxied</Badge> : <span className="text-micro text-fg-3">DNS only</span>}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </TableContainer>
              </div>
            )}
          </div>

          {/* Firewall events */}
          {events.length > 0 && (
            <div>
              <p className="mb-2 flex items-center gap-1 text-meta text-fg-2">
                <Shield size={12} aria-hidden="true" />
                Recent firewall events
              </p>
              <ul className="max-h-[200px] space-y-1 overflow-y-auto">
                {events.map((ev, i) => (
                  <li key={i} className="flex min-w-0 items-center gap-2 rounded-chip bg-surface-2 px-2 py-1.5 text-meta">
                    <Badge tone={ev.action === 'block' ? 'down' : 'neutral'}>{ev.action}</Badge>
                    <span className="shrink-0 font-mono text-fg-2">{ev.source}</span>
                    {ev.country && <span className="shrink-0 text-fg-3">{ev.country}</span>}
                    <span className={cn('min-w-0 flex-1 truncate text-fg-2')}>{ev.uri}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function CloudflareDetail({ data }: { data: CloudflareData }) {
  if (!data) return null;
  const totals = data.totals;
  const zones = data.zones ?? [];

  return (
    <div className="space-y-6">
      {/* Overview */}
      <StatGrid cols={5}>
        <StatTile label="Zones" value={isNum(data.zone_count) ? data.zone_count : null} />
        <StatTile label="Requests 24h" value={formatCount(totals?.requests)} />
        <StatTile label="Bandwidth 24h" value={formatBytes(totals?.bandwidth)} />
        <StatTile
          label="Threats 24h"
          value={formatCount(totals?.threats)}
          state={isNum(totals?.threats) && totals.threats > 0 ? 'warning' : undefined}
        />
        <StatTile label="Cache hit rate" value={isNum(totals?.cache_pct) ? totals.cache_pct : null} unit="%" />
      </StatGrid>

      {/* Zones */}
      <section>
        <SectionTitle meta={isNum(data.zone_count) ? `${data.zone_count}` : undefined}>Zones</SectionTitle>
        <div className="space-y-3">
          {zones.map((zone) => (
            <ZoneCard key={zone.id} zone={zone} />
          ))}
        </div>
      </section>
    </div>
  );
}
