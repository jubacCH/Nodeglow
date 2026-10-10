'use client';

import { cn } from '@/lib/utils';
import type { HealthState } from '@/lib/status';
import type { Agent } from '@/types';

/** Usage thresholds shared by the agent list and detail page. */
export function usageState(pct: number): HealthState {
  if (pct >= 90) return 'down';
  if (pct >= 75) return 'warning';
  return 'ok';
}

const FILL: Record<HealthState, string> = {
  ok: 'bg-ok',
  degraded: 'bg-degraded',
  warning: 'bg-warning',
  down: 'bg-down',
  maint: 'bg-maint',
  unknown: 'bg-unknown',
};

/**
 * Thin usage bar. `null` means "not reported": an em dash and a hatched track,
 * never an empty green bar. `stale` dims the value (agent offline, last report).
 */
export function UsageBar({
  label, value, stale, className,
}: { label: string; value: number | null | undefined; stale?: boolean; className?: string }) {
  const has = value != null && Number.isFinite(value);
  const pct = has ? Math.max(0, Math.min(100, value as number)) : 0;
  const state = has ? usageState(pct) : 'unknown';
  return (
    <div className={cn('min-w-0', className)}>
      <div className="mb-1 flex justify-between gap-2 text-meta">
        <span className="text-fg-2">{label}</span>
        <span className={cn('num', has ? 'text-fg' : 'text-fg-3', stale && 'opacity-60')}>
          {has ? `${Math.round(pct)}%` : '—'}
          {stale && has && <span className="sr-only"> (last report, outdated)</span>}
        </span>
      </div>
      <div
        className={cn('h-[5px] overflow-hidden rounded-pill', has ? 'bg-surface-3' : 'ng-hatch')}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={has ? Math.round(pct) : undefined}
        aria-valuetext={has ? `${Math.round(pct)}%` : 'Not reported'}
      >
        {has && <div className={cn('h-full rounded-pill', FILL[state], stale && 'opacity-50')} style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}

export type AgentState = { status: HealthState | 'disabled'; label: string };

/**
 * Agent connection state. An agent that never reported is "No data", not
 * offline and never green; a disabled agent is dimmed.
 */
export function agentState(agent: Pick<Agent, 'enabled' | 'online' | 'last_seen'>): AgentState {
  if (agent.enabled === false) return { status: 'disabled', label: 'Disabled' };
  if (agent.online) return { status: 'ok', label: 'Online' };
  if (!agent.last_seen) return { status: 'unknown', label: 'Never connected' };
  return { status: 'down', label: 'Offline' };
}
