'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { StatGrid, StatTile, TableCard, formatDate, isNum } from './parts';

type TaskResult = 'success' | 'partial' | 'failed' | 'cancelled' | 'none' | null;

interface SynobackupTask {
  source: 'hyper_backup' | 'active_backup';
  id: string;
  name: string;
  type: string;
  state: string | null;
  last_result: TaskResult;
  last_result_raw: string | null;
  last_run: string | null;
  next_run: string | null;
  scheduled: boolean | null;
  counts: { success_count?: number; warning_count?: number; error_count?: number } | null;
}

interface SynobackupData {
  collected_at?: string;
  sources?: { hyper_backup: boolean; active_backup: boolean };
  tasks: SynobackupTask[];
  totals?: { tasks: number; failed: number; partial: number };
}

const SOURCE_LABEL: Record<string, string> = {
  hyper_backup: 'Hyper Backup',
  active_backup: 'Active Backup for Business',
};

const RESULT_LABEL: Record<string, string> = {
  success: 'Success',
  partial: 'Partial',
  failed: 'Failed',
  cancelled: 'Cancelled',
  none: 'Never run',
};

/** Unknown, cancelled and "never run" are grey: none of them is healthy. */
function resultState(r: TaskResult | undefined): HealthState {
  if (r === 'success') return 'ok';
  if (r === 'partial') return 'warning';
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

function TaskTable({ title, tasks, collectedAt }: { title: string; tasks: SynobackupTask[]; collectedAt: string | undefined }) {
  return (
    <TableCard title={title} meta={`${tasks.length}`}>
      {tasks.length === 0 ? (
        <p className="px-4 pb-4 text-ui text-fg-3">No tasks configured.</p>
      ) : (
        <Table>
          <THead>
            <Tr>
              <Th>Task</Th>
              <Th>Type</Th>
              <Th>State</Th>
              <Th>Last result</Th>
              <Th>Last run</Th>
              <Th numeric>Age</Th>
              <Th>Next run</Th>
              <Th numeric>Items ok / warn / error</Th>
            </Tr>
          </THead>
          <TBody>
            {tasks.map((t) => (
              <Tr key={`${t.source}-${t.id || t.name}`}>
                <Td className="font-medium">{t.name || '—'}</Td>
                <Td muted className="whitespace-nowrap">{t.type || '—'}</Td>
                <Td muted className="whitespace-nowrap">
                  {t.state || '—'}
                  {t.scheduled === false && <Badge className="ml-2">Unscheduled</Badge>}
                </Td>
                <Td>
                  {t.last_result ? (
                    <StatusPill status={resultState(t.last_result)} size="sm">
                      {RESULT_LABEL[t.last_result] ?? t.last_result}
                    </StatusPill>
                  ) : t.last_result_raw ? (
                    <span className="text-fg-2" title="Result not recognised">{t.last_result_raw}</span>
                  ) : (
                    '—'
                  )}
                </Td>
                <Td muted className="whitespace-nowrap">{formatDate(t.last_run) ?? '—'}</Td>
                <Td numeric className="whitespace-nowrap">{ageText(t.last_run, collectedAt) ?? '—'}</Td>
                <Td muted className="whitespace-nowrap">{formatDate(t.next_run) ?? '—'}</Td>
                <Td numeric className="whitespace-nowrap">
                  {t.counts
                    ? [t.counts.success_count, t.counts.warning_count, t.counts.error_count]
                        .map((n) => (isNum(n) ? n : '—'))
                        .join(' / ')
                    : '—'}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}
    </TableCard>
  );
}

export function SynobackupDetail({ data }: { data: SynobackupData }) {
  const tasks = data.tasks ?? [];
  const totals = data.totals;
  const sources = data.sources;
  const nFailed = totals?.failed;
  const nPartial = totals?.partial;

  return (
    <div className="space-y-6">
      <StatGrid cols={3}>
        <StatTile label="Tasks" value={totals?.tasks ?? (tasks.length || null)} />
        <StatTile label="Last run failed" value={nFailed} state={isNum(nFailed) && nFailed > 0 ? 'down' : undefined} />
        <StatTile label="Last run partial" value={nPartial} state={isNum(nPartial) && nPartial > 0 ? 'warning' : undefined} />
      </StatGrid>

      {/* A source that could not be asked is not shown as an empty table: the page header lists it as unavailable. */}
      {(['hyper_backup', 'active_backup'] as const).map((src) =>
        sources?.[src] ? (
          <TaskTable
            key={src}
            title={SOURCE_LABEL[src]}
            tasks={tasks.filter((t) => t.source === src)}
            collectedAt={data.collected_at}
          />
        ) : (
          <Card key={src} as="section">
            <CardHeader title={SOURCE_LABEL[src]} />
            <p className="text-ui text-fg-3">Could not be read from this NAS.</p>
          </Card>
        ),
      )}
    </div>
  );
}
