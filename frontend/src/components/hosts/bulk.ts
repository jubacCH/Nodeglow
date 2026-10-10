/** Bulk host actions against PATCH /api/v1/hosts/bulk (pure helpers, tested). */

export interface BulkUpdates {
  maintenance?: boolean;
  enabled?: boolean;
  probe_id?: number | null;
  check_type?: string;
  latency_threshold_ms?: number | null;
}

export interface BulkResponse {
  ok?: boolean;
  updated: number;
  ids: number[];
  missing: number[];
  ignored_fields: string[];
}

export type BulkAction =
  | { kind: 'maintenance'; on: boolean }
  | { kind: 'enabled'; on: boolean }
  | { kind: 'probe'; probeId: number | null; probeName?: string }
  | { kind: 'edit'; updates: BulkUpdates }
  | { kind: 'delete' };

export function bulkUpdatesFor(action: Exclude<BulkAction, { kind: 'delete' }>): BulkUpdates {
  switch (action.kind) {
    case 'maintenance': return { maintenance: action.on };
    case 'enabled': return { enabled: action.on };
    case 'probe': return { probe_id: action.probeId };
    case 'edit': return action.updates;
  }
}

const plural = (n: number, word = 'host') => `${n} ${word}${n === 1 ? '' : 's'}`;

function verb(action: BulkAction): string {
  switch (action.kind) {
    case 'maintenance': return action.on ? 'Maintenance started for' : 'Maintenance ended for';
    case 'enabled': return action.on ? 'Monitoring enabled for' : 'Monitoring disabled for';
    case 'probe': return action.probeId == null ? 'Core now checks' : `${action.probeName ?? 'Probe'} now checks`;
    case 'edit': return 'Updated';
    case 'delete': return 'Deleted';
  }
}

export interface BulkReport {
  tone: 'success' | 'warning' | 'error';
  message: string;
  /** Details worth keeping on screen after the toast is gone. */
  details: string[];
}

/** Human summary of a bulk result, naming hosts that vanished and fields the server ignored. */
export function describeBulkResult(action: BulkAction, res: BulkResponse, names?: Map<number, string>): BulkReport {
  const details: string[] = [];
  if (res.missing.length > 0) {
    const shown = res.missing.slice(0, 5).map((id) => names?.get(id) ?? `#${id}`).join(', ');
    const more = res.missing.length > 5 ? ` and ${res.missing.length - 5} more` : '';
    details.push(`${plural(res.missing.length)} no longer exist${res.missing.length === 1 ? 's' : ''} and ${res.missing.length === 1 ? 'was' : 'were'} skipped: ${shown}${more}.`);
  }
  if (res.ignored_fields.length > 0) {
    details.push(`The server ignored ${res.ignored_fields.length === 1 ? 'the field' : 'the fields'} ${res.ignored_fields.join(', ')}.`);
  }
  if (res.updated === 0) {
    return { tone: 'error', message: 'No host was changed.', details };
  }
  return {
    tone: details.length > 0 ? 'warning' : 'success',
    message: `${verb(action)} ${plural(res.updated)}.`,
    details,
  };
}
