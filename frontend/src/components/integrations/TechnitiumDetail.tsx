'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, TableContainer, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { BlockedAllowedChart, KV, KVGrid, StatGrid, StatTile, StateLabel, TopList, fixed, formatCount, isNum } from './parts';

interface ClusterNode {
  name: string;
  type: string;
  state: string;
  version: string;
  ip: string;
  last_seen?: string | null;
}

interface TechnitiumData {
  status: string;
  version: string;
  server_domain: string;
  update_available: boolean;
  update_version: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  server_failures_today: number;
  nxdomain_today: number;
  clients: number;
  domains_blocked: number;
  zones: number;
  blocklist_next_update: string;
  top_queries: { domain: string; count: number }[];
  top_blocked: { domain: string; count: number }[];
  top_clients: { client: string; count: number }[];
  cluster_initialized: boolean;
  cluster_domain: string;
  cluster_nodes: ClusterNode[];
  cluster_nodes_unhealthy: number;
}

export function TechnitiumDetail({ data }: { data: TechnitiumData }) {
  return (
    <div className="space-y-6">
      {/* Stat tiles (last 24h) */}
      <StatGrid>
        <StatTile label="Queries (24h)" value={formatCount(data.queries_today)} />
        <StatTile label="Blocked (24h)" value={formatCount(data.blocked_today)} />
        <StatTile label="Block rate" value={fixed(data.blocked_pct)} unit="%" />
        <StatTile label="Domains on list" value={formatCount(data.domains_blocked)} />
      </StatGrid>

      {/* Service */}
      <Card as="section">
        <CardHeader title="Service" />
        <KVGrid>
          <KV label="Blocking">{data.status}</KV>
          <KV label="Clients (24h)">{isNum(data.clients) ? data.clients : null}</KV>
          <KV label="Server failures (24h)">{formatCount(data.server_failures_today)}</KV>
          <KV label="Version">
            {data.version ? (
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono">v{data.version}</span>
                {data.update_available ? (
                  <Badge tone="accent">Update to {data.update_version} available</Badge>
                ) : (
                  <span className="text-meta text-fg-3">up to date</span>
                )}
              </span>
            ) : null}
          </KV>
        </KVGrid>
      </Card>

      {/* Cluster */}
      {data.cluster_initialized && (
        <Card
          as="section"
          padding="none"
          glow={isNum(data.cluster_nodes_unhealthy) && data.cluster_nodes_unhealthy > 0 ? 'crit' : undefined}
        >
          <CardHeader
            title={`Cluster ${data.cluster_domain ?? ''}`.trim()}
            actions={
              isNum(data.cluster_nodes_unhealthy) && data.cluster_nodes_unhealthy > 0 ? (
                <StatusPill status="down">{data.cluster_nodes_unhealthy} node(s) unreachable</StatusPill>
              ) : undefined
            }
            className="mb-2 px-4 pt-4"
          />
          <TableContainer className="relative">
            <Table density="compact">
              <THead>
                <Tr>
                  <Th>State</Th>
                  <Th>Node</Th>
                  <Th>IP</Th>
                  <Th>Type</Th>
                  <Th numeric>Version</Th>
                </Tr>
              </THead>
              <TBody>
                {(data.cluster_nodes ?? []).map((node) => {
                  const healthy = node.state === 'Self' || node.state === 'Connected';
                  return (
                    <Tr key={node.name}>
                      <Td className="whitespace-nowrap">
                        <StateLabel status={!node.state ? 'unknown' : healthy ? 'ok' : 'down'}>{node.state || 'No data'}</StateLabel>
                      </Td>
                      <Td className="max-w-[240px] truncate font-mono">{node.name}</Td>
                      <Td muted className="whitespace-nowrap font-mono">{node.ip || '—'}</Td>
                      <Td muted>{node.type || '—'}</Td>
                      <Td numeric muted>{node.version ? `v${node.version}` : '—'}</Td>
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
          </TableContainer>
        </Card>
      )}

      {/* Chart + lists */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-4">
        <BlockedAllowedChart blocked={data.blocked_today} total={data.queries_today} />
        <TopList title="Top blocked domains" rows={(data.top_blocked ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top queries" rows={(data.top_queries ?? []).map((e) => ({ label: e.domain, count: e.count }))} />
        <TopList title="Top clients" rows={(data.top_clients ?? []).map((e) => ({ label: e.client, count: e.count }))} />
      </div>
    </div>
  );
}
