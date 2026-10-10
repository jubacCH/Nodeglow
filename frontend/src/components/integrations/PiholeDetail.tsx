'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import Link from 'next/link';
import { BlockedAllowedChart, KV, KVGrid, StatGrid, StatTile, TopList, fixed, formatCount } from './parts';

interface PiholeData {
  status: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  domains_blocked: number;
  dns_queries_all_types: number;
  clients: number;
  gravity_last_updated: string;
  api_version: number;
  top_queries: { domain: string; count: number }[];
  top_blocked: { domain: string; count: number }[];
  local_dns?: { domain: string; ip: string; type?: string }[];
  reply_types?: Record<string, number>;
}

/** Pi-hole blocking status: enabled = ok, disabled = warning (no filtering), missing = no data. */
function BlockingStatus({ status }: { status: string | null | undefined }) {
  if (!status) return <StatusPill status="unknown" />;
  return <StatusPill status={status === 'enabled' ? 'ok' : 'warning'}>{status}</StatusPill>;
}

export function PiholeDetail({ data }: { data: PiholeData }) {
  return (
    <div className="space-y-6">
      {/* Stat tiles */}
      <StatGrid>
        <StatTile label="Total queries" value={formatCount(data.queries_today)} />
        <StatTile label="Blocked" value={formatCount(data.blocked_today)} />
        <StatTile label="Block rate" value={fixed(data.blocked_pct)} unit="%" />
        <StatTile label="Domains on list" value={formatCount(data.domains_blocked)} />
      </StatGrid>

      {/* Status */}
      <Card as="section">
        <CardHeader title="Service" />
        <KVGrid>
          <KV label="Blocking"><BlockingStatus status={data.status} /></KV>
          <KV label="Clients">{data.clients}</KV>
          <KV label="API version" mono>{data.api_version != null ? `v${data.api_version}` : null}</KV>
          <KV label="Gravity updated">{data.gravity_last_updated}</KV>
        </KVGrid>
      </Card>

      {/* Chart + lists */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <BlockedAllowedChart blocked={data.blocked_today} total={data.queries_today} />
        <TopList title="Top blocked domains" rows={(data.top_blocked ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top queries" rows={(data.top_queries ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
      </div>

      {/* Local DNS */}
      {data.local_dns && data.local_dns.length > 0 && (
        <Card as="section">
          <CardHeader title="Local DNS records" meta={`${data.local_dns.length}`} />
          <ul className="grid grid-cols-1 gap-x-6 gap-y-1 md:grid-cols-2">
            {data.local_dns.map((entry, i) => (
              <li key={i} className="flex min-w-0 items-center gap-2 py-1 text-ui">
                {entry.type === 'CNAME' && <Badge tone="accent">CNAME</Badge>}
                <span className="truncate font-mono text-fg-2">{entry.domain}</span>
                <span className="shrink-0 text-fg-3" aria-hidden="true">→</span>
                <Link href={'/hosts?q=' + encodeURIComponent(entry.ip)} className="truncate font-mono text-accent hover:underline">
                  {entry.ip}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
