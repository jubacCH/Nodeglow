import type { AgentServiceState } from '@/types';

/** Mirrors the backend/agent validation (services/agent_services.py). */
const NAME_RE = /^[A-Za-z0-9_.@:$ \\-]+$/;
export const MAX_WATCHED_SERVICES = 50;

export function isValidServiceName(name: string): boolean {
  return name.length > 0 && name.length <= 256 && !name.startsWith('-') && NAME_RE.test(name);
}

/** Parse the edit box: one name per line or comma-separated, trimmed, de-duplicated. */
export function parseServiceList(text: string): { names: string[]; invalid: string[] } {
  const names: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\n,]/)) {
    const name = raw.trim();
    if (!name || names.includes(name) || invalid.includes(name)) continue;
    if (isValidServiceName(name)) names.push(name);
    else invalid.push(name);
  }
  return { names, invalid };
}

export type ServiceBadge = { label: string; severity: 'critical' | 'warning' | 'info' | 'ok' | 'none' };

/** Badge for one watched service. `null` state = the agent never reported it. */
export function serviceBadge(state: AgentServiceState['state']): ServiceBadge {
  switch (state) {
    case 'running': return { label: 'Running', severity: 'ok' };
    case 'stopped': return { label: 'Stopped', severity: 'critical' };
    case 'not_found': return { label: 'Not found', severity: 'warning' };
    case 'unknown': return { label: 'Unknown', severity: 'info' };
    default: return { label: 'No data', severity: 'none' };
  }
}
