'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, SectionTitle, StatGrid, StatTile, TableCard, UsageBar, formatDate, isNum, usageState } from './parts';

type VeeamResult = 'success' | 'warning' | 'failed' | 'none' | null;

interface VeeamServer {
  name: string;
  build: string;
  vbr_id: string;
  patches: string[];
}

interface VeeamJob {
  id: string;
  name: string;
  description: string;
  type: string;
  workload: string;
  status: string | null;
  last_result: VeeamResult;
  last_run: string | null;
  next_run: string | null;
  repository_name: string;
  objects_count: number | null;
}

interface VeeamRepository {
  id: string;
  name: string;
  type: string;
  host: string;
  path: string;
  capacity_gb: number | null;
  free_gb: number | null;
  used_gb: number | null;
  pct: number | null;
}

interface VeeamSession {
  id: string;
  name: string;
  type: string;
  state: string;
  result: VeeamResult;
  message: string;
  created_at: string | null;
  ended_at: string | null;
}

interface VeeamData {
  collected_at?: string;
  server: VeeamServer | null;
  jobs: VeeamJob[];
  repositories: VeeamRepository[] | null;
  sessions: VeeamSession[] | null;
  totals?: { jobs: number; jobs_failed: number; jobs_warning: number; jobs_disabled: number };
}

const RESULT_LABEL: Record<string, string> = { success: 'Success', warning: 'Warning', failed: 'Failed', none: 'Never run' };

/** Unknown (missing) and "never run" are both grey: neither is healthy. */
function resultState(r: VeeamResult | undefined): HealthState {
  if (r === 'success') return 'ok';
  if (r === 'warning') return 'warning';
  if (r === 'failed') return 'down';
  return 'unknown';
}

function ageText(iso: string | null | undefined, collectedAt: string | undefined): string | null {
  if (!iso || !collectedAt) return null;
  const ms = new Date(collectedAt).getTime() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return null;
  const h = ms / 3_600_000;
  if (h < 1) return `${Math.max(0, Math.round(h * 60))} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

function gb(v: number | null | undefined): string | null {
  if (!isNum(v)) return null;
  return v >= 1024 ? `${(v / 1024).toFixed(2)} TB` : `${v.toFixed(1)} GB`;
}

function ResultPill({ result }: { result: VeeamResult | undefined }) {
  if (result === undefined || result === null) return <span className="text-fg-3">—</span>;
  return (
    <StatusPill status={resultState(result)} size="sm">
      {RESULT_LABEL[result] ?? result}
    </StatusPill>
  );
}

export function VeeamDetail({ data }: { data: VeeamData }) {
  const jobs = data.jobs ?? [];
  const repos = data.repositories;
  const sessions = data.sessions;
  const server = data.server;
  const totals = data.totals;
  const collectedAt = data.collected_at;
  const nFailed = totals?.jobs_failed;
  const nWarning = totals?.jobs_warning;

  return (
    <div className="space-y-6">
      <Card as="section">
        <CardHeader title="Backup server" />
        <KVGrid className="md:grid-cols-3">
          <KV label="Name" mono>{server?.name}</KV>
          <KV label="Build" mono>{server?.build}</KV>
          <KV label="Patches">{server && server.patches.length > 0 ? server.patches.join(', ') : null}</KV>
        </KVGrid>
      </Card>

      <StatGrid>
        <StatTile label="Jobs" value={totals?.jobs ?? (jobs.length || null)} />
        <StatTile
          label="Last run failed"
          value={nFailed}
          state={isNum(nFailed) && nFailed > 0 ? 'down' : undefined}
        />
        <StatTile
          label="Last run with warnings"
          value={nWarning}
          state={isNum(nWarning) && nWarning > 0 ? 'warning' : undefined}
        />
        <StatTile label="Disabled" value={totals?.jobs_disabled} />
      </StatGrid>

      {repos && repos.length > 0 && (
        <section>
          <SectionTitle>Repositories</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {repos.map((r) => (
              <Card key={r.id || r.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{r.name}</span>
                  {r.type && <Badge>{r.type}</Badge>}
                </div>
                <UsageBar
                  label="Usage"
                  pct={r.pct}
                  state={usageState(r.pct, 85, 95)}
                  detail={isNum(r.pct) && isNum(r.used_gb) && isNum(r.capacity_gb)
                    ? `${gb(r.used_gb)} / ${gb(r.capacity_gb)} (${r.pct.toFixed(1)} %)`
                    : undefined}
                />
                <KVGrid className="md:grid-cols-2">
                  <KV label="Free">{gb(r.free_gb)}</KV>
                  <KV label="Host" mono>{r.host}</KV>
                  <KV label="Path" mono>{r.path}</KV>
                </KVGrid>
              </Card>
            ))}
          </div>
        </section>
      )}
      {repos && repos.length === 0 && (
        <Card as="section">
          <CardHeader title="Repositories" />
          <p className="text-ui text-fg-3">No repositories reported.</p>
        </Card>
      )}

      <TableCard title="Jobs" meta={`${jobs.length}`}>
        {jobs.length === 0 ? (
          <p className="px-4 pb-4 text-ui text-fg-3">No jobs reported.</p>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Job</Th>
                <Th>Type</Th>
                <Th>State</Th>
                <Th>Last result</Th>
                <Th>Last run</Th>
                <Th numeric>Age</Th>
                <Th>Next run</Th>
                <Th>Repository</Th>
              </Tr>
            </THead>
            <TBody>
              {jobs.map((j) => (
                <Tr key={j.id || j.name}>
                  <Td className="font-medium">{j.name || '—'}</Td>
                  <Td muted className="whitespace-nowrap">{j.type || '—'}</Td>
                  <Td muted className="whitespace-nowrap">{j.status || '—'}</Td>
                  <Td><ResultPill result={j.last_result} /></Td>
                  <Td muted className="whitespace-nowrap">{formatDate(j.last_run) ?? '—'}</Td>
                  <Td numeric className="whitespace-nowrap">{ageText(j.last_run, collectedAt) ?? '—'}</Td>
                  <Td muted className="whitespace-nowrap">{formatDate(j.next_run) ?? '—'}</Td>
                  <Td muted>{j.repository_name || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </TableCard>

      {sessions && (
        <TableCard title="Recent sessions" meta={`${sessions.length}`}>
          {sessions.length === 0 ? (
            <p className="px-4 pb-4 text-ui text-fg-3">No recent sessions.</p>
          ) : (
            <Table>
              <THead>
                <Tr>
                  <Th>Session</Th>
                  <Th>Type</Th>
                  <Th>State</Th>
                  <Th>Result</Th>
                  <Th>Started</Th>
                  <Th>Ended</Th>
                  <Th>Message</Th>
                </Tr>
              </THead>
              <TBody>
                {sessions.map((s) => (
                  <Tr key={s.id || `${s.name}-${s.created_at}`}>
                    <Td className="font-medium">{s.name || '—'}</Td>
                    <Td muted className="whitespace-nowrap">{s.type || '—'}</Td>
                    <Td muted className="whitespace-nowrap">{s.state || '—'}</Td>
                    <Td><ResultPill result={s.result} /></Td>
                    <Td muted className="whitespace-nowrap">{formatDate(s.created_at) ?? '—'}</Td>
                    <Td muted className="whitespace-nowrap">{formatDate(s.ended_at) ?? '—'}</Td>
                    <Td muted className="wrap-break-word">{s.message || '—'}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          )}
        </TableCard>
      )}
    </div>
  );
}
