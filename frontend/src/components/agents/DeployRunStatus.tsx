'use client';

import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { get } from '@/lib/api';
import { runOutcome, type DeployJob, type DeployRun } from '@/lib/agentDeploy';
import { cn } from '@/lib/utils';

const OUTCOME_TEXT = {
  running: 'Installing…',
  ok: 'Installed.',
  failed: 'The installation failed.',
  partial: 'Installed on some targets; see the ones that failed.',
} as const;

function JobIcon({ status }: { status: DeployJob['status'] }) {
  if (status === 'ok') return <CheckCircle2 size={14} className="shrink-0 text-ok" aria-label="Installed" />;
  if (status === 'failed') return <XCircle size={14} className="shrink-0 text-down" aria-label="Failed" />;
  return (
    <Loader2
      size={14}
      className="shrink-0 animate-spin text-fg-3 motion-reduce:animate-none"
      aria-label={status === 'queued' ? 'Waiting' : 'Installing'}
    />
  );
}

/** Follows a deploy run (GET /api/agent-deploy/{runId}) until every target is done. */
export function DeployRunStatus({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: ['agent-deploy', runId],
    queryFn: () => get<DeployRun>(`/api/agent-deploy/${encodeURIComponent(runId)}`),
    refetchInterval: (q) => (q.state.data?.done ? false : 2000),
  });
  const run = query.data;
  if (!run) {
    return <p className="text-meta text-fg-3">{query.isError ? 'Could not load the deploy status.' : 'Starting…'}</p>;
  }
  const outcome = runOutcome(run);
  return (
    <div className="space-y-2" aria-live="polite">
      <p
        role="status"
        className={cn(
          'rounded-ctl border px-3 py-2 text-meta',
          outcome === 'ok' && 'border-ok/30 bg-ok-soft text-ok',
          outcome === 'running' && 'border-border bg-surface-2 text-fg-2',
          (outcome === 'failed' || outcome === 'partial') && 'border-warning/30 bg-warning-soft text-warning',
        )}
      >
        {OUTCOME_TEXT[outcome]}
      </p>
      <ul className="max-h-[320px] space-y-1 overflow-y-auto">
        {run.jobs.map((job) => (
          <li key={job.id} className="rounded-chip px-2 py-1 text-meta hover:bg-surface-2">
            <div className="flex min-w-0 items-center gap-2">
              <JobIcon status={job.status} />
              <span className="min-w-0 flex-1 truncate text-fg">
                {job.kind === 'proxmox_lxc' ? `CT ${job.target}` : job.target}
              </span>
              <span className="shrink-0 text-micro text-fg-3">{job.status}</span>
            </div>
            {job.detail && (
              <details className="mt-1" open={job.status === 'failed'}>
                <summary className="cursor-pointer text-micro text-fg-3">Output</summary>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-ctl border border-border bg-surface-2 p-2 font-code text-micro text-fg-2">
                  {job.detail}
                </pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
