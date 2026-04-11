'use client';

import { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  RefreshCw,
  Zap,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
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

const HOURS_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: '1h' },
  { value: 24, label: '24h' },
  { value: 168, label: '7d' },
  { value: 720, label: '30d' },
];

const SOURCE_META: Record<
  TimelineEventType,
  { label: string; icon: typeof Activity; color: string }
> = {
  status: { label: 'Status', icon: Activity, color: 'text-sky-400' },
  incident: { label: 'Incidents', icon: Zap, color: 'text-amber-400' },
  syslog: { label: 'Syslog', icon: FileText, color: 'text-violet-400' },
};

const SEVERITY_STYLES: Record<
  TimelineSeverity,
  { dot: string; ring: string; text: string }
> = {
  critical: {
    dot: 'bg-red-500',
    ring: 'ring-red-500/30',
    text: 'text-red-400',
  },
  error: {
    dot: 'bg-orange-500',
    ring: 'ring-orange-500/30',
    text: 'text-orange-400',
  },
  warning: {
    dot: 'bg-amber-500',
    ring: 'ring-amber-500/30',
    text: 'text-amber-400',
  },
  info: {
    dot: 'bg-sky-500',
    ring: 'ring-sky-500/30',
    text: 'text-sky-400',
  },
};

export function HostTimeline({ hostId }: HostTimelineProps) {
  const [hours, setHours] = useState<number>(24);
  const [activeSources, setActiveSources] = useState<TimelineEventType[]>([
    'status',
    'incident',
    'syslog',
  ]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const { data, isLoading, refetch, isFetching, dataUpdatedAt } = useHostTimeline(
    hostId,
    hours,
    activeSources,
  );

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
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {/* Timespan toggle */}
        <div className="flex items-center gap-1 p-1 rounded-lg bg-white/[0.04] border border-white/[0.06]">
          {HOURS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setHours(opt.value)}
              className={cn(
                'px-3 py-1 text-xs font-medium rounded-md transition-colors',
                hours === opt.value
                  ? 'bg-sky-500/20 text-sky-300'
                  : 'text-slate-400 hover:text-slate-200',
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Source filter pills */}
        <div className="flex items-center gap-1.5">
          {(['status', 'incident', 'syslog'] as TimelineEventType[]).map((src) => {
            const meta = SOURCE_META[src];
            const active = activeSources.includes(src);
            const Icon = meta.icon;
            return (
              <button
                key={src}
                onClick={() => toggleSource(src)}
                className={cn(
                  'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors',
                  active
                    ? 'bg-white/[0.08] border-white/[0.14] text-slate-100'
                    : 'bg-white/[0.02] border-white/[0.06] text-slate-500 hover:text-slate-300',
                )}
              >
                <Icon size={12} className={active ? meta.color : undefined} />
                {meta.label}
              </button>
            );
          })}
        </div>

        {/* Live / refresh */}
        <div className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {hours <= 1 && (
            <span className="inline-flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              LIVE
            </span>
          )}
          {dataUpdatedAt > 0 && (
            <span title={new Date(dataUpdatedAt).toLocaleString()}>
              Updated {timeAgo(new Date(dataUpdatedAt).toISOString())}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1 rounded-md hover:bg-white/[0.06] text-slate-400 hover:text-slate-200 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : activeSources.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title="No sources selected"
          description="Select at least one event source above to see the timeline."
        />
      ) : !data?.events?.length ? (
        <EmptyState
          icon={Activity}
          title="No events"
          description={`Nothing happened on this host in the last ${hoursLabel(hours)} matching your filter.`}
        />
      ) : (
        <div className="relative">
          {/* Vertical rail */}
          <div
            aria-hidden
            className="absolute left-[11px] top-2 bottom-2 w-px"
            style={{ background: 'var(--ng-card-border)' }}
          />
          <div className="space-y-3">
            {grouped.map((group) => (
              <div key={group.day}>
                <div className="flex items-center gap-2 mb-2 ml-7">
                  <span className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">
                    {group.day}
                  </span>
                  <span className="text-[10px] text-slate-600">
                    {group.events.length} event{group.events.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="space-y-1">
                  {group.events.map((event, idx) => {
                    const key = `${event.ts}-${event.type}-${idx}`;
                    const isOpen = expanded.has(key);
                    const sev = SEVERITY_STYLES[event.severity] ?? SEVERITY_STYLES.info;
                    const meta = SOURCE_META[event.type];
                    const Icon = meta.icon;
                    return (
                      <div key={key} className="relative">
                        {/* Dot on the rail */}
                        <span
                          className={cn(
                            'absolute left-[7px] top-3 w-2 h-2 rounded-full ring-2',
                            sev.dot,
                            sev.ring,
                          )}
                        />
                        <button
                          onClick={() => toggleExpand(key)}
                          className="w-full flex items-start gap-2 pl-7 pr-2 py-2 rounded-md hover:bg-white/[0.03] transition-colors text-left"
                        >
                          {isOpen ? (
                            <ChevronDown size={12} className="text-slate-500 mt-1 shrink-0" />
                          ) : (
                            <ChevronRight size={12} className="text-slate-500 mt-1 shrink-0" />
                          )}
                          <Icon size={12} className={cn('mt-1 shrink-0', meta.color)} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-baseline gap-2">
                              <span className={cn('text-xs font-medium truncate', sev.text)}>
                                {event.title}
                              </span>
                              <span className="text-[10px] text-slate-600 font-mono shrink-0">
                                {formatTime(event.ts)}
                              </span>
                            </div>
                            {event.summary && (
                              <p className="text-[11px] text-slate-500 truncate mt-0.5">
                                {event.summary}
                              </p>
                            )}
                            {isOpen && (
                              <pre className="mt-2 p-2 rounded-md bg-white/[0.03] border border-white/[0.06] text-[10px] text-slate-400 font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                                {JSON.stringify(event.details, null, 2)}
                              </pre>
                            )}
                          </div>
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
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
  if (h <= 168) return `${Math.round(h / 24)} days`;
  return `${Math.round(h / 24)} days`;
}
