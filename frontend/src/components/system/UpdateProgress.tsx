'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Circle, Loader2, RefreshCw } from 'lucide-react';
import { get } from '@/lib/api';
import {
  bannerFor,
  isRestartGap,
  isTerminal,
  stepLabel,
  type UpdateRunState,
  type UpdateStep,
} from '@/lib/updateSteps';

const POLL_INTERVAL_MS = 2000;

function StepIcon({ status }: { status: UpdateStep['status'] }) {
  if (status === 'ok') return <Check size={14} aria-hidden="true" className="shrink-0 text-ok" />;
  if (status === 'failed') return <AlertTriangle size={14} aria-hidden="true" className="shrink-0 text-down" />;
  if (status === 'running') return <Loader2 size={14} aria-hidden="true" className="shrink-0 animate-spin text-accent" />;
  return <Circle size={14} aria-hidden="true" className="shrink-0 text-fg-3" />;
}

const BANNER_STYLES: Record<string, string> = {
  running: 'border-border-2 bg-surface-2 text-fg',
  restarting: 'border-degraded/30 bg-degraded-soft text-degraded',
  success: 'border-ok/30 bg-ok-soft text-ok',
  error: 'border-down/30 bg-down-soft text-down',
};

const STEP_STATE_LABEL: Record<UpdateStep['status'], string> = {
  pending: 'pending',
  running: 'running',
  ok: 'done',
  failed: 'failed',
};

export function UpdateProgress({
  active,
  onFinished,
}: {
  active: boolean;
  onFinished: (state: UpdateRunState) => void;
}) {
  const [state, setState] = useState<UpdateRunState | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const stoppedRef = useRef(false);

  const poll = useCallback(async (): Promise<boolean> => {
    try {
      const next = await get<UpdateRunState>('/api/update/status');
      if (!next || !Array.isArray(next.steps)) return false;
      setPollFailed(false);
      setState(next);
      if (isTerminal(next)) {
        onFinished(next);
        return true;
      }
    } catch {
      // Expected while the backend restarts — bannerFor renders that as a gap.
      setPollFailed(true);
    }
    return false;
  }, [onFinished]);

  useEffect(() => {
    if (!active) return;
    stoppedRef.current = false;

    const id = setInterval(async () => {
      if (stoppedRef.current) return;
      const done = await poll();
      if (done) {
        stoppedRef.current = true;
        clearInterval(id);
      }
    }, POLL_INTERVAL_MS);

    void poll();
    return () => {
      stoppedRef.current = true;
      clearInterval(id);
    };
  }, [active, poll]);

  if (!state || state.steps.length === 0) return null;

  const banner = bannerFor(state, pollFailed);
  const restarting = isRestartGap(state, pollFailed);

  return (
    <div className="mt-3 space-y-3">
      {banner && (
        <div role={banner.kind === 'error' ? 'alert' : 'status'} className={`rounded-ctl border px-3 py-2 text-meta ${BANNER_STYLES[banner.kind]}`}>
          <div className="flex items-center gap-2 font-medium">
            {banner.kind === 'restarting' && <RefreshCw size={13} aria-hidden="true" className="animate-spin" />}
            {banner.title}
          </div>
          {banner.detail && <p className="mt-1 break-words text-fg-2">{banner.detail}</p>}
        </div>
      )}

      <ol className="space-y-1.5" aria-label="Update steps">
        {state.steps.map((step) => (
          <li key={step.name} className="flex items-start gap-2 text-meta">
            <span className="mt-0.5">
              <StepIcon status={restarting && step.name === 'restart' ? 'running' : step.status} />
            </span>
            <span className="min-w-0">
              <span
                className={
                  step.status === 'pending'
                    ? 'text-fg-3'
                    : step.status === 'failed'
                      ? 'text-down'
                      : 'text-fg'
                }
              >
                {stepLabel(step.name)}
                <span className="sr-only"> ({STEP_STATE_LABEL[restarting && step.name === 'restart' ? 'running' : step.status]})</span>
              </span>
              {step.detail && (
                <span className="block break-words font-mono text-fg-3">{step.detail}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
