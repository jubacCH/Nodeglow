'use client';

import { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  RefreshCw,
  Settings2,
  Zap,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryErrorState } from '@/components/ui/QueryState';
import { SegmentedControl } from '@/components/ui/Tabs';
import { cn, timeAgo } from '@/lib/utils';
import {
  useHostTimeline,
  type TimelineEvent,
  type TimelineEventType,
  type TimelineSeverity,
} from '@/hooks/queries/useHosts';

interface HostTimelineProps {
  hostId: number;
}

const HOURS_OPTIONS: { value: string; label: string }[] = [
  { value: '1', label: '1h' },
  { value: '24', label: '24h' },
  { value: '168', label: '7d' },
  { value: '720', label: '30d' },
  { value: '2160', label: '90d' },
  { value: '8760', label: '1y' },
];

// Sources are categories, not states: neutral icons, no status colours.
const SOURCE_META: Record<TimelineEventType, { label: string; icon: typeof Activity }> = {
  status: { label: 'Status', icon: Activity },
  incident: { label: 'Incidents', icon: Zap },
  syslog: { label: 'Syslog', icon: FileText },
  change: { label: 'Changes', icon: Settings2 },
};

const SEVERITY_STYLES: Record<TimelineSeverity, { dot: string; text: string; label: string }> = {
  critical: { dot: 'bg-down', text: 'text-down', label: 'Critical' },
  error: { dot: 'bg-warning', text: 'text-warning', label: 'Error' },
  warning: { dot: 'bg-degraded', text: 'text-degraded', label: 'Warning' },
  info: { dot: 'bg-line', text: 'text-fg', label: 'Info' },
};

export function HostTimeline({ hostId }: HostTimelineProps) {
  const [hours, setHours] = useState<number>(24);
  const [activeSources, setActiveSources] = useState<TimelineEventType[]>([
    'status',
    'incident',
    'syslog',
    'change',
  ]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } =
    useHostTimeline(hostId, hours, activeSources);

  const toggleSource = (src: TimelineEventType) => {
    setActiveSources((prev) =>
      prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src],
    );
  };

  const toggleExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const grouped = useMemo(() => groupByDay(data?.events ?? []), [data]);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SegmentedControl
          label="Timeline range"
          options={HOURS_OPTIONS}
          value={String(hours)}
          onChange={(v) => setHours(Number(v))}
        />
        <div role="group" aria-label="Event sources" className="flex flex-wrap items-center gap-1.5">
          {(['status', 'incident', 'syslog', 'change'] as TimelineEventType[]).map((src) => {
            const meta = SOURCE_META[src];
            const active = activeSources.includes(src);
            const Icon = meta.icon;
            return (
              <button
                key={src}
                type="button"
                aria-pressed={active}
                onClick={() => toggleSource(src)}
                className={cn(
                  'inline-flex h-[28px] items-center gap-1.5 rounded-pill border px-2.5 text-meta font-medium transition-colors',
                  active
                    ? 'border-accent/40 bg-accent-soft text-accent'
                    : 'border-border bg-surface text-fg-2 hover:bg-surface-2 hover:text-fg',
                )}
              >
                <Icon size={12} aria-hidden="true" />
                {meta.label}
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex items-center gap-2 text-meta text-fg-3">
          {hours <= 1 && <span>Refreshes every 30 s</span>}
          {dataUpdatedAt > 0 && (
            <span title={new Date(dataUpdatedAt).toLocaleString()}>
              Updated {timeAgo(new Date(dataUpdatedAt).toISOString())}
            </span>
          )}
          <IconButton size="sm" aria-label="Refresh timeline" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw size={13} aria-hidden="true" className={isFetching ? 'animate-spin' : undefined} />
          </IconButton>
        </div>
      </div>

      <Card padding="sm">
        {isLoading ? (
          <div className="space-y-2" aria-busy="true" aria-label="Loading timeline">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isError ? (
          // A failed query must never look like a quiet host.
          <QueryErrorState error={error} onRetry={refetch} title="Timeline unavailable" />
        ) : activeSources.length === 0 ? (
          <EmptyState
            icon={AlertTriangle}
            variant="no-results"
            title="No sources selected"
            description="Select at least one event source above to see the timeline."
          />
        ) : !data?.events?.length ? (
          <EmptyState
            icon={Activity}
            variant="no-results"
            title="No events"
            description={`Nothing recorded for this host in the last ${hoursLabel(hours)} for the selected sources.`}
          />
        ) : (
          <div className="relative">
            <div aria-hidden="true" className="absolute bottom-2 left-[11px] top-2 w-px bg-border-2" />
            <div className="space-y-3">
              {grouped.map((group) => (
                <section key={group.day} aria-label={group.day}>
                  <div className="mb-1.5 ml-7 flex items-baseline gap-2">
                    <h3 className="text-meta font-medium text-fg-2">{group.day}</h3>
                    <span className="text-micro text-fg-3">
                      {group.events.length} event{group.events.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <ul className="space-y-0.5">
                    {group.events.map((event, idx) => {
                      const key = `${event.ts}-${event.type}-${idx}`;
                      const isOpen = expanded.has(key);
                      const sev = SEVERITY_STYLES[event.severity] ?? SEVERITY_STYLES.info;
                      const meta = SOURCE_META[event.type];
                      const Icon = meta.icon;
                      return (
                        <li key={key} className="relative">
                          <span
                            aria-hidden="true"
                            className={cn('absolute left-[7px] top-3 h-2 w-2 rounded-full ring-2 ring-surface', sev.dot)}
                          />
                          <button
                            type="button"
                            aria-expanded={isOpen}
                            onClick={() => toggleExpand(key)}
                            className="flex w-full items-start gap-2 rounded-ng-sm py-2 pl-7 pr-2 text-left transition-colors hover:bg-surface-2"
                          >
                            {isOpen ? (
                              <ChevronDown size={12} aria-hidden="true" className="mt-1 shrink-0 text-fg-3" />
                            ) : (
                              <ChevronRight size={12} aria-hidden="true" className="mt-1 shrink-0 text-fg-3" />
                            )}
                            <Icon size={12} aria-hidden="true" className="mt-1 shrink-0 text-fg-3" />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-baseline gap-2">
                                <span className={cn('truncate text-ui font-medium', sev.text)}>
                                  <span className="sr-only">{meta.label}, {sev.label}: </span>
                                  {event.title}
                                </span>
                                <span className="num shrink-0 font-mono text-micro text-fg-3">
                                  {formatTime(event.ts)}
                                </span>
                              </div>
                              {event.summary && (
                                <p className="mt-0.5 truncate text-meta text-fg-2">{event.summary}</p>
                              )}
                              {isOpen && (
                                <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded-ng-sm border border-border bg-surface-2 p-2 font-mono text-micro text-fg-2">
                                  {JSON.stringify(event.details, null, 2)}
                                </pre>
                              )}
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

function groupByDay(events: TimelineEvent[]): { day: string; events: TimelineEvent[] }[] {
  const map = new Map<string, TimelineEvent[]>();
  for (const e of events) {
    const d = new Date(e.ts);
    if (Number.isNaN(d.getTime())) continue;
    const key = d.toLocaleDateString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    });
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(e);
  }
  return Array.from(map.entries()).map(([day, events]) => ({ day, events }));
}

function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function hoursLabel(h: number): string {
  if (h <= 1) return 'hour';
  if (h <= 24) return `${h}h`;
  if (h >= 8760) return 'year';
  return `${Math.round(h / 24)} days`;
}
