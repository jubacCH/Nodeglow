'use client';

import React from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { RefreshCw, ChevronDown, ChevronUp, AlertTriangle, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';

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

type StatusDotStatus = 'online' | 'offline' | 'maintenance' | 'unknown' | 'disabled' | 'error';

function statusDotStatus(status: string): StatusDotStatus {
  switch (status) {
    case 'ok': return 'online';
    case 'warning': return 'maintenance';
    case 'failed': return 'offline';
    default: return 'unknown';
  }
}

function statusSeverity(status: string): 'info' | 'warning' | 'critical' {
  switch (status) {
    case 'ok': return 'info';
    case 'warning': return 'warning';
    case 'failed': return 'critical';
    default: return 'info';
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
  const [syncing, setSyncing] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ['backups'],
    queryFn: () => get<BackupsData>('/api/backups'),
  });

  const { data: compliance } = useQuery({
    queryKey: ['backups-compliance'],
    queryFn: () => get<ComplianceData>('/api/backups/compliance'),
  });

  const summary = data?.summary ?? { total: 0, healthy: 0, warning: 0, failed: 0, unknown: 0 };
  const jobs = [...(data?.jobs ?? [])].sort(
    (a, b) => (statusSortOrder[a.effective_status] ?? 9) - (statusSortOrder[b.effective_status] ?? 9),
  );

  async function syncNow() {
    setSyncing(true);
    try {
      await post('/api/backups/sync');
      qc.invalidateQueries({ queryKey: ['backups'] });
      qc.invalidateQueries({ queryKey: ['backups-compliance'] });
    } finally {
      setSyncing(false);
    }
  }

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const hasComplianceIssues =
    (compliance?.overdue?.length ?? 0) > 0 ||
    (compliance?.failed?.length ?? 0) > 0 ||
    (compliance?.never_run?.length ?? 0) > 0;

  return (
    <div>
      <PageHeader
        title="Backup Monitoring"
        description="Track backup health across infrastructure"
        actions={
          <Button variant="ghost" size="sm" onClick={syncNow} disabled={syncing}>
            <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing...' : 'Sync Now'}
          </Button>
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-emerald-400">{summary.healthy}</p>
          <p className="text-xs text-slate-400 mt-1">Healthy</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className={`text-2xl font-semibold ${summary.warning > 0 ? 'text-amber-400' : 'text-slate-500'}`}>
            {summary.warning}
          </p>
          <p className="text-xs text-slate-400 mt-1">Warning / Overdue</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className={`text-2xl font-semibold ${summary.failed > 0 ? 'text-red-400' : 'text-slate-500'}`}>
            {summary.failed}
          </p>
          <p className="text-xs text-slate-400 mt-1">Failed</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-slate-100">{summary.total}</p>
          <p className="text-xs text-slate-400 mt-1">Total Jobs</p>
        </GlassCard>
      </div>

      {/* Compliance alerts */}
      {hasComplianceIssues && (
        <div className="space-y-3 mb-6">
          {(compliance?.failed ?? []).map((job) => (
            <GlassCard key={`fail-${job.id}`} className="p-4 border-l-4 border-red-500/60">
              <div className="flex items-start gap-3">
                <XCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-red-400">{job.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Target: {job.target_name}
                    {job.last_error && <span className="text-red-400/80"> &mdash; {job.last_error}</span>}
                  </p>
                </div>
              </div>
            </GlassCard>
          ))}
          {(compliance?.overdue ?? []).map((job) => (
            <GlassCard key={`overdue-${job.id}`} className="p-4 border-l-4 border-amber-500/60">
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="text-amber-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-amber-400">{job.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Target: {job.target_name}
                    {job.hours_since_last != null && (
                      <span className="text-amber-400/80"> &mdash; {job.hours_since_last}h since last run (expected every {job.expected_frequency_hours}h)</span>
                    )}
                  </p>
                </div>
              </div>
            </GlassCard>
          ))}
          {(compliance?.never_run ?? []).map((job) => (
            <GlassCard key={`never-${job.id}`} className="p-4 border-l-4 border-slate-500/60">
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="text-slate-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-300">{job.name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Target: {job.target_name} &mdash; Never executed
                  </p>
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {/* Jobs table */}
      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="w-8 px-2 py-3" />
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Target</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Source</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Storage</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Last Run</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Duration</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Size</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Frequency</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-2 py-3" />
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-36" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12 ml-auto" /></td>
                  </tr>
                ))}
              {jobs.map((job) => {
                const isExpanded = expanded.has(job.id);
                return (
                  <React.Fragment key={job.id}>
                    <tr
                      className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors cursor-pointer"
                      onClick={() => toggle(job.id)}
                    >
                      <td className="px-2 py-3 text-slate-500">
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <StatusDot status={statusDotStatus(job.effective_status)} />
                          <Badge variant="severity" severity={statusSeverity(job.effective_status)}>
                            {job.effective_status}
                          </Badge>
                          {!job.enabled && (
                            <span className="text-[10px] text-slate-500 uppercase">disabled</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div>
                          <span className="text-slate-200">{job.target_name}</span>
                          {job.target_vmid != null && (
                            <span className="ml-1.5 text-xs text-slate-500">VM {job.target_vmid}</span>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mt-0.5">{job.name}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className="px-1.5 py-0.5 rounded bg-white/[0.06] text-[10px] font-medium text-slate-300 uppercase">
                          {job.source_type}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-400 text-xs">{job.storage_name || '—'}</td>
                      <td className="px-4 py-3">
                        <span className="text-slate-300 text-xs">{formatRelativeTime(job.last_run_at)}</span>
                        {job.overdue && (
                          <span className="ml-1.5 text-[10px] text-amber-400 font-medium">OVERDUE</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-xs text-slate-400">
                        {formatDuration(job.last_duration_sec)}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-xs text-slate-400">
                        {formatBytes(job.last_size_bytes)}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-slate-400">
                        {job.expected_frequency_hours}h
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`detail-${job.id}`} className="border-b border-white/[0.06]">
                        <td colSpan={9} className="p-0">
                          <JobHistory jobId={job.id} />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {!isLoading && jobs.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-sm text-slate-500">
                    No backup jobs found. Configure integrations to start monitoring backups.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}

/* ─── Inline history detail ─── */

function JobHistory({ jobId }: { jobId: number }) {
  const { data: raw, isLoading } = useQuery<{ entries: HistoryEntry[] }>({
    queryKey: ['backup-history', jobId],
    queryFn: () => get(`/api/backups/${jobId}/history`),
    staleTime: 5 * 60_000,
  });
  const data = raw?.entries;

  if (isLoading) {
    return (
      <div className="px-6 py-4 bg-white/[0.02] space-y-2">
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="px-6 py-4 bg-white/[0.02]">
        <p className="text-sm text-slate-500">No history available for this job.</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-4 bg-white/[0.02]">
      <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-3">
        Recent Backup History
      </h4>
      <div className="space-y-2">
        {data.slice(0, 10).map((entry, i) => (
          <div key={i} className="flex items-center gap-4 text-xs">
            <StatusDot status={statusDotStatus(entry.status)} />
            <span className="text-slate-400 w-32 shrink-0">{formatRelativeTime(entry.timestamp)}</span>
            <Badge variant="severity" severity={statusSeverity(entry.status)}>
              {entry.status}
            </Badge>
            <span className="font-mono text-slate-400">{formatDuration(entry.duration_sec)}</span>
            <span className="font-mono text-slate-400">{formatBytes(entry.size_bytes)}</span>
            {entry.error && (
              <span className="text-red-400 truncate max-w-xs" title={entry.error}>
                {entry.error}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
