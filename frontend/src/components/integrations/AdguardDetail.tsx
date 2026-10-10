'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { BlockedAllowedChart, KV, KVGrid, StatGrid, StatTile, TopList, fixed, formatCount, withUnit } from './parts';

interface AdguardEntry {
  domain: string;
  count: number;
}

interface AdguardData {
  status: string;
  version: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  avg_processing_time_ms: number;
  top_queries: AdguardEntry[];
  top_blocked: AdguardEntry[];
  clients_today: number;
  filtering_enabled: boolean;
  safebrowsing_enabled: boolean;
  parental_enabled: boolean;
  num_replaced_safebrowsing: number;
  num_replaced_parental: number;
}

function ServiceStatus({ status }: { status: string | null | undefined }) {
  if (!status) return <StatusPill status="unknown" />;
  return <StatusPill status={status === 'running' ? 'ok' : 'warning'}>{status}</StatusPill>;
}

export function AdguardDetail({ data }: { data: AdguardData }) {
  const features = [
    data.filtering_enabled && 'Filtering',
    data.safebrowsing_enabled && 'Safe browsing',
    data.parental_enabled && 'Parental',
  ].filter(Boolean) as string[];

  return (
    <div className="space-y-6">
      {/* Stat tiles */}
      <StatGrid>
        <StatTile label="Total queries" value={formatCount(data.queries_today)} />
        <StatTile label="Blocked" value={formatCount(data.blocked_today)} />
        <StatTile label="Block rate" value={fixed(data.blocked_pct)} unit="%" />
        <StatTile label="Clients today" value={data.clients_today} />
      </StatGrid>

      {/* Chart + lists */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <BlockedAllowedChart blocked={data.blocked_today} total={data.queries_today} />
        <TopList title="Top blocked domains" rows={(data.top_blocked ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top queries" rows={(data.top_queries ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
      </div>

      {/* Info row */}
      <Card as="section">
        <CardHeader title="Service info" />
        <KVGrid>
          <KV label="Status"><ServiceStatus status={data.status} /></KV>
          <KV label="Version" mono>{data.version}</KV>
          <KV label="Avg processing">{withUnit(data.avg_processing_time_ms, 'ms')}</KV>
          <KV label="Features">
            {features.length > 0 ? (
              <span className="flex flex-wrap gap-1">
                {features.map((f) => <Badge key={f}>{f}</Badge>)}
              </span>
            ) : (
              <span className="text-fg-3">None enabled</span>
            )}
          </KV>
        </KVGrid>
      </Card>
    </div>
  );
}
