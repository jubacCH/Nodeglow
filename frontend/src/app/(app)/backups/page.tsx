'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Archive, Clock, RefreshCw, XCircle } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardHeader } from '@/components/ui/Card';
import { Tag } from '@/components/ui/Tag';
import { Button, buttonClasses } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { BigNumber } from '@/components/ui/BigNumber';
import { StatusPill } from '@/components/ui/StatusPill';
import { SidePanel } from '@/components/ui/SidePanel';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, QueryErrorState, formatAsOf } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { get, post, apiErrorMessage } from '@/lib/api';
import type { HealthState } from '@/lib/status';
import { useToastStore } from '@/stores/toast';

/* ─── Types ─── */

interface BackupSummary {
  total: number;
  healthy: number;
  warning: number;
  failed: number;
  unknown: number;
}

interface BackupJob {
  id: number;
  name: string;
  source_type: string;
  target_name: string;
  target_vmid: number | null;
  storage_name: string;
  last_run_at: string | null;
  last_status: string;
  last_duration_sec: number | null;
  last_size_bytes: number | null;
  last_error: string | null;
  expected_frequency_hours: number;
  enabled: boolean;
  effective_status: string;
  hours_since_last: number | null;
  overdue: boolean;
}

interface BackupsData {
  summary: BackupSummary;
  jobs: BackupJob[];
}

interface ComplianceData {
  overdue: BackupJob[];
  failed: BackupJob[];
  never_run: BackupJob[];
}

interface HistoryEntry {
  timestamp: string;
  status: string;
  duration_sec: number | null;
  size_bytes: number | null;
  error: string | null;
}

/* ─── Format helpers ─── */

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return `${size.toFixed(1)} ${units[i]}`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

function formatRelativeTime(isoDate: string | null): string {
  if (!isoDate) return 'Never';
  const diff = Date.now() - new Date(isoDate).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/* ─── Status helpers ─── */

/** Backup state; "unknown" (no run recorded) is never shown as healthy. */
function jobState(status: string, enabled = true): { status: HealthState | 'disabled'; label: string } {
  if (!enabled) return { status: 'disabled', label: 'Disabled' };
  switch (status) {
    case 'ok': return { status: 'ok', label: 'OK' };
    case 'warning': return { status: 'warning', label: 'Warning' };
    case 'failed': return { status: 'down', label: 'Failed' };
    default: return { status: 'unknown', label: 'No data' };
  }
}

const statusSortOrder: Record<string, number> = {
  failed: 0,
  warning: 1,
  ok: 2,
  unknown: 3,
};

/* ─── Page ─── */

export default function BackupsPage() {
  useEffect(() => { document.title = 'Backups | Nodeglow'; }, []);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const [syncing, setSyncing] = useState(false);
  const [selected, setSelected] = useState<BackupJob | null>(null);

  const query = useQuery({
    queryKey: ['backups'],
    queryFn: () => get<BackupsData>('/api/backups'),
  });
  const { data } = query;

  const { data: compliance } = useQuery({
    queryKey: ['backups-compliance'],
    queryFn: () => get<ComplianceData>('/api/backups/compliance'),
  });

  const summary = data?.summary;
  const jobs = useMemo(() => [...(data?.jobs ?? [])].sort(
    (a, b) => (statusSortOrder[a.effective_status] ?? 9) - (statusSortOrder[b.effective_status] ?? 9),
  ), [data]);

  async function syncNow() {
    setSyncing(true);
    try {
      await post('/api/backups/sync');
      qc.invalidateQueries({ queryKey: ['backups'] });
      qc.invalidateQueries({ queryKey: ['backups-compliance'] });
    } catch (e) {
      toast(apiErrorMessage(e, 'Backup sync failed'), 'error');
    } finally {
      setSyncing(false);
    }
  }

  const failed = compliance?.failed ?? [];
  const overdue = compliance?.overdue ?? [];
  const neverRun = compliance?.never_run ?? [];
  const hasComplianceIssues = failed.length + overdue.length + neverRun.length > 0;
  const asOf = formatAsOf(query.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Backups"
        description={`Backup health across infrastructure, from connected integrations${asOf ? ` · updated ${asOf}` : ''}`}
        actions={
          <Button variant="secondary" size="sm" onClick={syncNow} loading={syncing}>
            {!syncing && <RefreshCw size={14} aria-hidden="true" />}
            {syncing ? 'Syncing…' : 'Sync now'}
          </Button>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
        <Card padding="sm">
          <BigNumber size="sm" value={summary?.failed} state={summary?.failed ? 'down' : undefined} label="Failed" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={summary?.warning} state={summary?.warning ? 'warning' : undefined} label="Warning / overdue" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={summary?.healthy} label="Healthy" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={summary?.unknown} label="No data" />
        </Card>
        <Card padding="sm" className="col-span-2 md:col-span-1">
          <BigNumber size="sm" value={summary?.total} label="Total jobs" />
        </Card>
      </div>

      {hasComplianceIssues && (
        <Card
          as="section"
          aria-labelledby="backup-attention"
          className="mb-4"
          glow={failed.length ? 'crit' : overdue.length ? 'warn' : undefined}
        >
          <CardHeader
            title="Needs attention"
            titleId="backup-attention"
            meta={`${failed.length + overdue.length + neverRun.length} job${failed.length + overdue.length + neverRun.length === 1 ? '' : 's'}`}
          />
          <ul className="divide-y divide-border">
            {failed.map((job) => (
              <li key={`fail-${job.id}`} className="flex items-start gap-3 py-2.5">
                <XCircle size={16} className="mt-0.5 shrink-0 text-down" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="text-ui font-medium text-fg">{job.name} <span className="font-normal text-down">failed</span></p>
                  <p className="mt-0.5 break-words text-meta text-fg-2">
                    Target: {job.target_name}
                    {job.last_error && <span className="text-down"> — {job.last_error}</span>}
                  </p>
                </div>
              </li>
            ))}
            {overdue.map((job) => (
              <li key={`overdue-${job.id}`} className="flex items-start gap-3 py-2.5">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="text-ui font-medium text-fg">{job.name} <span className="font-normal text-warning">overdue</span></p>
                  <p className="mt-0.5 text-meta text-fg-2">
                    Target: {job.target_name}
                    {job.hours_since_last != null && (
                      <> — {job.hours_since_last}h since last run (expected every {job.expected_frequency_hours}h)</>
                    )}
                  </p>
                </div>
              </li>
            ))}
            {neverRun.map((job) => (
              <li key={`never-${job.id}`} className="flex items-start gap-3 py-2.5">
                <Clock size={16} className="mt-0.5 shrink-0 text-fg-3" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="text-ui font-medium text-fg">{job.name} <span className="font-normal text-fg-2">never ran</span></p>
                  <p className="mt-0.5 text-meta text-fg-2">Target: {job.target_name} — no backup has been recorded</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card padding="none">
        <QueryState
          query={{ ...query, data: data ? jobs : undefined }}
          errorTitle="Could not load backup jobs"
          loading={
            <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading backup jobs">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              icon={Archive}
              title="No backup jobs found"
              description="Backups are read from integrations such as Proxmox. Connect one to start monitoring backups."
              action={<Link href="/integration/store" className={buttonClasses({ variant: 'secondary', size: 'sm' })}>Browse integrations</Link>}
            />
          }
        >
          {(rows) => (
            <TableContainer className="relative">
              <Table className="min-w-[900px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Target</Th>
                    <Th>Source</Th>
                    <Th>Storage</Th>
                    <Th>Last run</Th>
                    <Th numeric>Duration</Th>
                    <Th numeric>Size</Th>
                    <Th numeric>Frequency</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((job) => {
                    const st = jobState(job.effective_status, job.enabled);
                    return (
                      <Tr key={job.id} selected={selected?.id === job.id}>
                        <Td><StatusPill status={st.status}>{st.label}</StatusPill></Td>
                        <Td className="max-w-[280px] py-1.5">
                          <button
                            type="button"
                            onClick={() => setSelected(job)}
                            className="block max-w-full truncate text-left font-medium text-fg hover:text-accent"
                            aria-label={`Backup history for ${job.target_name}`}
                          >
                            {job.target_name}
                            {job.target_vmid != null && <span className="ml-1.5 text-meta font-normal text-fg-3">VM {job.target_vmid}</span>}
                          </button>
                          <span className="block truncate text-meta text-fg-3">{job.name}</span>
                        </Td>
                        <Td><Tag>{job.source_type}</Tag></Td>
                        <Td muted className="text-meta">{job.storage_name || '—'}</Td>
                        <Td className="whitespace-nowrap">
                          {job.last_run_at ? (
                            <time dateTime={job.last_run_at} title={new Date(job.last_run_at).toLocaleString()} className="text-fg-2">
                              {formatRelativeTime(job.last_run_at)}
                            </time>
                          ) : (
                            <span className="text-fg-3">Never</span>
                          )}
                          {job.overdue && <StatusPill status="warning" size="sm" className="ml-1.5">Overdue</StatusPill>}
                        </Td>
                        <Td numeric muted>{formatDuration(job.last_duration_sec)}</Td>
                        <Td numeric muted>{formatBytes(job.last_size_bytes)}</Td>
                        <Td numeric muted>{job.expected_frequency_hours}h</Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>

      <SidePanel
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.target_name ?? 'Backup job'}
        ariaLabel={`Backup history ${selected?.target_name ?? ''}`}
        meta={selected ? `${selected.name} · ${selected.source_type} · every ${selected.expected_frequency_hours}h` : undefined}
      >
        {selected && (
          <>
            {selected.last_error && (
              <p role="alert" className="mb-4 break-words rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-meta text-down">
                Last error: {selected.last_error}
              </p>
            )}
            <JobHistory jobId={selected.id} />
          </>
        )}
      </SidePanel>
    </div>
  );
}

/* ─── History detail ─── */

function JobHistory({ jobId }: { jobId: number }) {
  const query = useQuery<{ entries: HistoryEntry[] }>({
    queryKey: ['backup-history', jobId],
    queryFn: () => get(`/api/backups/${jobId}/history`),
    staleTime: 5 * 60_000,
  });
  const data = query.data?.entries;

  if (query.isLoading) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading history">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-3/4" />
      </div>
    );
  }

  if (query.isError && !data) {
    return <QueryErrorState compact error={query.error} onRetry={query.refetch} title="Could not load history" />;
  }

  if (!data || data.length === 0) {
    return <EmptyState compact title="No history yet" description="No runs have been recorded for this job." />;
  }

  return (
    <section aria-labelledby="backup-history-title">
      <h3 id="backup-history-title" className="mb-2 text-ui font-medium text-fg">Recent runs</h3>
      <TableContainer className="relative">
        <Table density="compact">
          <THead>
            <Tr>
              <Th>Status</Th>
              <Th>When</Th>
              <Th numeric>Duration</Th>
              <Th numeric>Size</Th>
            </Tr>
          </THead>
          <TBody>
            {data.slice(0, 10).map((entry, i) => {
              const st = jobState(entry.status);
              return (
                <Tr key={i}>
                  <Td>
                    <StatusPill status={st.status} size="sm">{st.label}</StatusPill>
                  </Td>
                  <Td muted className="whitespace-nowrap">
                    <time dateTime={entry.timestamp} title={new Date(entry.timestamp).toLocaleString()}>{formatRelativeTime(entry.timestamp)}</time>
                    {entry.error && <span className="block max-w-[220px] truncate text-micro text-down" title={entry.error}>{entry.error}</span>}
                  </Td>
                  <Td numeric muted>{formatDuration(entry.duration_sec)}</Td>
                  <Td numeric muted>{formatBytes(entry.size_bytes)}</Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableContainer>
    </section>
  );
}
