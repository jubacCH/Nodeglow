import type { Agent } from '@/types';

/** Probe freshness as GET /api/v1/agents reports it (null for non-probes). */
export interface ProbeInfo {
  stale: boolean;
  staleness_window_seconds: number;
  last_report: string | null;
  host_count: number | null;
}

export type ProbeAgent = Agent & { probe?: ProbeInfo | null };

/** "probe-a" or "probe-a (silent)" — a stale probe is never offered silently. */
export function probeOptionLabel(p: ProbeAgent): string {
  return p.probe?.stale ? `${p.name} (silent)` : p.name;
}

export function probeById(agents: ProbeAgent[] | undefined, id: number | null | undefined): ProbeAgent | undefined {
  if (id == null) return undefined;
  return (agents ?? []).find((a) => a.id === id);
}
