'use client';

/** Incident pieces shared by the overview and the dashboard side panel. */
import { Info, TriangleAlert } from 'lucide-react';
import { formatDuration, incidentHostsLabel, severityLabel, severityTone } from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { DashboardIncident } from '@/types/dashboard';

/** Severity tile of an incident; the glow sits here, on the object (E3). */
export function SeverityIcon({ severity, acknowledged, size = 'md' }: { severity: string; acknowledged?: boolean; size?: 'md' | 'lg' }) {
  const tone = severityTone(severity);
  const Icon = tone === 'info' ? Info : TriangleAlert;
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid shrink-0 place-items-center',
        size === 'md' ? 'h-[30px] w-[30px] rounded-ctl' : 'h-11 w-11 rounded-card',
        tone === 'crit' && 'bg-down-soft text-down ng-glow-crit',
        tone === 'warn' && 'bg-warning-soft text-warning ng-glow-warn',
        tone === 'info' && 'bg-surface-2 text-fg-2',
        tone !== 'info' && acknowledged && 'ng-glow-acked',
      )}
    >
      <Icon size={size === 'md' ? 16 : 22} />
    </span>
  );
}

export function IncidentRow({ inc, onOpen }: { inc: DashboardIncident; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="-mx-2.5 grid w-[calc(100%+20px)] grid-cols-[auto_1fr_auto] items-center gap-3 rounded-ctl p-2.5 text-left hover:bg-surface-2"
    >
      <SeverityIcon severity={inc.severity} acknowledged={inc.acknowledged} />
      <span className="min-w-0">
        <span className="block truncate text-ui font-medium text-fg">{inc.title}</span>
        <span className="block truncate text-meta text-fg-2">
          #{inc.id} · {severityLabel(inc.severity)} · {incidentHostsLabel(inc.host_count)}
        </span>
      </span>
      <span className="whitespace-nowrap text-right text-meta text-fg-2">
        <span className="num">{formatDuration(inc.age_seconds)}</span>
        {inc.acknowledged && <span className="block text-micro text-accent">Acknowledged</span>}
      </span>
    </button>
  );
}
