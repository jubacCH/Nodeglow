'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, SectionTitle, StatGrid, StatTile, TableCard, UsageBar, formatBytes, formatDate, isNum, uptime, usageState } from './parts';

interface PbsVersion {
  version: string;
  release: string;
  repoid: string;
}

interface PbsNode {
  uptime_s: number | null;
  cpu_pct: number | null;
  mem_total: number | null;
  mem_used: number | null;
  root_total: number | null;
  root_used: number | null;
  load_1m: number | null;
  kernel: string | null;
}

interface PbsGroup {
  type: string;
  id: string;
  /** Namespace, "" for the root; part of ``name`` when set. */
  ns?: string;
  name: string;
  backup_count: number | null;
  last_backup: string | null;
  owner: string;
  comment: string;
  verification: 'ok' | 'failed' | 'unverified' | null;
  verify_failed_count: number | null;
}

interface PbsDatastore {
  name: string;
  mount_status: string;
  error: string | null;
  total: number | null;
  used: number | null;
  avail: number | null;
  pct: number | null;
  estimated_full: string | null;
  groups: PbsGroup[] | null;
  snapshots: { total: number; verified_ok: number; verify_failed: number; unverified: number } | null;
}

interface PbsTask {
  upid: string;
  type: string;
  worker_id: string;
  user: string;
  started_at: string | null;
  ended_at: string | null;
  status: string;
  result: 'ok' | 'warning' | 'error';
  superseded?: boolean;
}

interface PbsData {
  collected_at?: string;
  version: PbsVersion | null;
  node: PbsNode | null;
  datastores: PbsDatastore[];
  tasks: {
    window_hours: number;
    total: number;
    failed: number;
    warnings: number;
    truncated: boolean;
    issues: PbsTask[];
  } | null;
  /** groups / verify_failed_groups are null when a datastore's groups or snapshots could not be read. */
  totals?: { datastores: number; groups: number | null; verify_failed_groups: number | null };
}

/** Age of a timestamp relative to the collection time ("5 h", "2.3 d"), or null. */
function ageText(iso: string | null | undefined, collectedAt: string | undefined): string | null {
  if (!iso || !collectedAt) return null;
  const ms = new Date(collectedAt).getTime() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return null;
  const h = ms / 3_600_000;
  if (h < 1) return `${Math.max(0, Math.round(h * 60))} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

function verifyState(v: PbsGroup['verification']): HealthState {
  if (v === 'ok') return 'ok';
  if (v === 'failed') return 'down';
  return 'unknown';
}

const VERIFY_LABEL: Record<string, string> = { ok: 'Verified', failed: 'Failed', unverified: 'Not verified' };

function taskState(r: PbsTask['result']): HealthState {
  return r === 'error' ? 'down' : r === 'warning' ? 'warning' : 'ok';
}

export function PbsDetail({ data }: { data: PbsData }) {
  const datastores = data.datastores ?? [];
  const version = data.version;
  const node = data.node;
  const tasks = data.tasks;
  const collectedAt = data.collected_at;
  const totals = data.totals;

  const cpu = node?.cpu_pct;
  const memUsed = node?.mem_used;
  const memTotal = node?.mem_total;
  const verifyFailedGroups = totals?.verify_failed_groups;

  const activeFailures = tasks ? tasks.issues.filter((t) => t.result === 'error' && !t.superseded) : null;

  return (
    <div className="space-y-6">
      <Card as="section">
        <CardHeader title="Server" />
        <KVGrid className="md:grid-cols-3 lg:grid-cols-5">
          <KV label="Version" mono>{version ? `${version.version}-${version.release}` : null}</KV>
          <KV label="Kernel" mono>{node?.kernel}</KV>
          <KV label="Uptime">{uptime(node?.uptime_s)}</KV>
          <KV label="CPU">{isNum(cpu) ? `${cpu.toFixed(1)} %` : null}</KV>
          <KV label="Memory">
            {isNum(memUsed) && isNum(memTotal) ? `${formatBytes(memUsed)} / ${formatBytes(memTotal)}` : null}
          </KV>
        </KVGrid>
      </Card>

      <StatGrid>
        <StatTile label="Datastores" value={totals?.datastores ?? (datastores.length || null)} />
        <StatTile label="Backup groups" value={totals?.groups} />
        <StatTile
          label="Groups failing verification"
          value={verifyFailedGroups}
          state={isNum(verifyFailedGroups) && verifyFailedGroups > 0 ? 'down' : undefined}
        />
        <StatTile
          label={`Failed tasks (${tasks?.window_hours ?? 24} h)`}
          value={activeFailures ? activeFailures.length : null}
          state={activeFailures && activeFailures.length > 0 ? 'down' : undefined}
        />
      </StatGrid>

      {datastores.length > 0 && (
        <section>
          <SectionTitle>Datastores</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {datastores.map((ds) => (
              <Card key={ds.name} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{ds.name}</span>
                  {ds.error ? (
                    <StatusPill status="down">Error</StatusPill>
                  ) : ds.mount_status === 'notmounted' ? (
                    <StatusPill status="unknown">Not mounted</StatusPill>
                  ) : (
                    <StatusPill status={usageState(ds.pct, 85, 95)} />
                  )}
                </div>
                {ds.error && <p className="wrap-break-word text-meta text-down">{ds.error}</p>}
                <UsageBar
                  label="Usage"
                  pct={ds.pct}
                  state={usageState(ds.pct, 85, 95)}
                  detail={isNum(ds.pct) && isNum(ds.used) && isNum(ds.total)
                    ? `${formatBytes(ds.used)} / ${formatBytes(ds.total)} (${ds.pct.toFixed(1)} %)`
                    : undefined}
                />
                <KVGrid className="md:grid-cols-2">
                  <KV label="Estimated full">{formatDate(ds.estimated_full, 'date')}</KV>
                  <KV label="Snapshots">{ds.snapshots ? `${ds.snapshots.total}` : null}</KV>
                  <KV label="Verified">{ds.snapshots ? `${ds.snapshots.verified_ok}` : null}</KV>
                  <KV label="Not verified">{ds.snapshots ? `${ds.snapshots.unverified}` : null}</KV>
                </KVGrid>
              </Card>
            ))}
          </div>
        </section>
      )}

      {datastores.map((ds) =>
        ds.groups && ds.groups.length > 0 ? (
          <TableCard key={`g-${ds.name}`} title={`Backup groups: ${ds.name}`} meta={`${ds.groups.length}`}>
            <Table>
              <THead>
                <Tr>
                  <Th>Group</Th>
                  <Th numeric>Snapshots</Th>
                  <Th>Last backup</Th>
                  <Th numeric>Age</Th>
                  <Th>Verification</Th>
                  <Th>Owner</Th>
                </Tr>
              </THead>
              <TBody>
                {ds.groups.map((g) => (
                  <Tr key={g.name}>
                    <Td className="font-mono">{g.name}</Td>
                    <Td numeric>{isNum(g.backup_count) ? g.backup_count : '—'}</Td>
                    <Td muted className="whitespace-nowrap">{formatDate(g.last_backup) ?? '—'}</Td>
                    <Td numeric className="whitespace-nowrap">{ageText(g.last_backup, collectedAt) ?? '—'}</Td>
                    <Td>
                      {g.verification ? (
                        <StatusPill status={verifyState(g.verification)} size="sm">
                          {VERIFY_LABEL[g.verification] ?? g.verification}
                        </StatusPill>
                      ) : (
                        '—'
                      )}
                    </Td>
                    <Td muted className="whitespace-nowrap">{g.owner || '—'}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableCard>
        ) : ds.groups ? (
          <Card key={`g-${ds.name}`} as="section">
            <CardHeader title={`Backup groups: ${ds.name}`} />
            <p className="text-ui text-fg-3">No backup groups in this datastore.</p>
          </Card>
        ) : null,
      )}

      {tasks && (
        <TableCard
          title={`Tasks with problems (${tasks.window_hours} h)`}
          meta={`${tasks.total} finished, ${tasks.failed} failed, ${tasks.warnings} with warnings${tasks.truncated ? ', list truncated' : ''}`}
        >
          {tasks.issues.length === 0 ? (
            <p className="px-4 pb-4 text-ui text-fg-3">No failed tasks or warnings.</p>
          ) : (
            <Table>
              <THead>
                <Tr>
                  <Th>Type</Th>
                  <Th>Worker</Th>
                  <Th>Ended</Th>
                  <Th>Result</Th>
                  <Th>Status</Th>
                </Tr>
              </THead>
              <TBody>
                {tasks.issues.map((t) => (
                  <Tr key={t.upid || `${t.type}-${t.ended_at}`}>
                    <Td className="font-mono">{t.type}</Td>
                    <Td muted className="font-mono text-meta">{t.worker_id || '—'}</Td>
                    <Td muted className="whitespace-nowrap">{formatDate(t.ended_at) ?? '—'}</Td>
                    <Td>
                      <StatusPill status={taskState(t.result)} size="sm">
                        {t.result === 'error' ? 'Failed' : 'Warnings'}
                      </StatusPill>
                      {t.superseded && <Badge className="ml-2">Superseded</Badge>}
                    </Td>
                    <Td muted className="wrap-break-word">{t.status}</Td>
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
