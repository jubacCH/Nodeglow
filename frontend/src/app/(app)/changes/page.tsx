'use client';

import Link from 'next/link';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  Bot, CheckCircle2, Eye, Network, Plus, Radio, Settings2, TriangleAlert, Wrench, type LucideIcon,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Pagination } from '@/components/ui/Pagination';
import { QueryErrorState, StaleDataBanner } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { useChanges } from '@/hooks/queries/useDashboard';
import {
  CHANGE_LABEL, CHANGE_ORDER, changeDetail, changeHref, formatClock, formatWhen, parseTime, severityLabel,
} from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { ChangeItem, ChangeType } from '@/types/dashboard';

const PAGE_SIZE = 50;

const ICON: Record<ChangeType, LucideIcon> = {
  incident_opened: TriangleAlert,
  incident_resolved: CheckCircle2,
  incident_acknowledged: Eye,
  host_added: Plus,
  port_discovered: Network,
  agent_enrolled: Bot,
  agent_updated: Bot,
  maintenance_started: Wrench,
  probe_silent: Radio,
  config_change: Settings2,
};

type Range = 'visit' | '24h' | '7d' | '31d';
const RANGE_HOURS: Record<Exclude<Range, 'visit'>, number> = { '24h': 24, '7d': 24 * 7, '31d': 24 * 31 };

function dayLabel(iso: string | null): string {
  const t = parseTime(iso);
  if (t === null) return 'Unknown time';
  const d = new Date(t);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
}

function meta(i: ChangeItem): string {
  const d = i.detail ?? {};
  const parts: string[] = [];
  if (typeof d.severity === 'string') parts.push(severityLabel(d.severity));
  if (typeof d.rule === 'string') parts.push(d.rule);
  if (typeof d.by === 'string') parts.push(`by ${d.by}`);
  if (typeof d.source === 'string' && i.type === 'host_added') parts.push(d.discovered ? `discovered via ${d.source}` : 'added manually');
  if (typeof d.scope === 'string') parts.push(d.scope);
  return parts.join(' · ');
}

function ChangeRow({ item }: { item: ChangeItem }) {
  const Icon = ICON[item.type] ?? Settings2;
  const href = changeHref(item);
  const sev = typeof item.detail?.severity === 'string' ? item.detail.severity : null;
  const tone = item.type === 'incident_opened' && sev === 'critical' ? 'text-down'
    : item.type === 'incident_opened' && sev === 'warning' ? 'text-warning'
      : item.type === 'incident_resolved' ? 'text-ok'
        : item.type === 'probe_silent' ? 'text-unknown' : 'text-fg-2';
  const m = meta(item);
  const title = changeDetail(item);
  return (
    <li className="grid grid-cols-[32px_1fr_auto] items-center gap-3 border-b border-border px-5 py-2.5 last:border-b-0 max-[759px]:px-4">
      <span aria-hidden="true" className={cn('grid h-8 w-8 place-items-center rounded-ctl bg-surface-2', tone)}>
        <Icon size={16} />
      </span>
      <span className="min-w-0">
        {href ? (
          <Link href={href} className="block truncate text-ui font-medium text-fg hover:text-accent">{title}</Link>
        ) : (
          <span className="block truncate text-ui font-medium text-fg">{title}</span>
        )}
        <span className="block truncate text-meta text-fg-2">
          {CHANGE_LABEL[item.type]}{m ? ` · ${m}` : ''}
        </span>
      </span>
      <time dateTime={item.at ?? undefined} className="num whitespace-nowrap text-meta text-fg-2">{formatClock(item.at)}</time>
    </li>
  );
}

function ChangesView() {
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const sinceParam = search.get('since');
  const typesParam = search.get('types');
  // The window ends when the page was opened, so pages stay stable.
  const [until] = useState(() => new Date().toISOString());
  const [range, setRange] = useState<Range>(sinceParam ? 'visit' : '24h');
  const [page, setPage] = useState(0);
  const types = useMemo(
    () => (typesParam ? typesParam.split(',').filter((t): t is ChangeType => (CHANGE_ORDER as string[]).includes(t)) : []),
    [typesParam],
  );

  const since = useMemo(() => {
    if (range === 'visit' && sinceParam) return sinceParam;
    const hours = RANGE_HOURS[range === 'visit' ? '24h' : range];
    return new Date(parseTime(until)! - hours * 3_600_000).toISOString();
  }, [range, sinceParam, until]);

  useEffect(() => setPage(0), [since, typesParam]);

  const all = useChanges({ since, until, limit: 1 });
  const list = useChanges({ since, until, types, limit: PAGE_SIZE, offset: page * PAGE_SIZE });

  const setTypes = (next: ChangeType[]) => {
    const qs = new URLSearchParams(search.toString());
    if (next.length) qs.set('types', next.join(','));
    else qs.delete('types');
    router.replace(`${pathname}${qs.toString() ? `?${qs}` : ''}`, { scroll: false });
  };
  const toggle = (t: ChangeType) => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t]);

  const counts = all.data?.counts ?? {};
  const available = CHANGE_ORDER.filter((t) => t in counts);

  const groups = useMemo(() => {
    const out: { day: string; items: ChangeItem[] }[] = [];
    for (const it of list.data?.items ?? []) {
      const day = dayLabel(it.at);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(it);
      else out.push({ day, items: [it] });
    }
    return out;
  }, [list.data]);

  const rangeOptions = [
    ...(sinceParam ? [{ value: 'visit' as const, label: `Since ${formatWhen(sinceParam, Date.now())}` }] : []),
    { value: '24h' as const, label: '24 h' },
    { value: '7d' as const, label: '7 days' },
    { value: '31d' as const, label: '31 days' },
  ];

  return (
    <div>
      <PageHeader
        title="Changes"
        description={`What changed from ${formatWhen(since, Date.now())} to ${formatClock(until)} — derived from incidents, hosts, agents, maintenance and the audit log.`}
        actions={<SegmentedControl label="Time range" options={rangeOptions} value={range} onChange={setRange} />}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Filter by change type">
        <button
          type="button"
          aria-pressed={types.length === 0}
          onClick={() => setTypes([])}
          className={cn('inline-flex h-[28px] items-center gap-1.5 rounded-pill border px-2.5 text-meta font-medium',
            types.length === 0 ? 'border-accent/40 bg-accent-soft text-accent' : 'border-border-2 bg-surface text-fg-2 hover:text-fg')}
        >
          All <span className="num">{all.data?.total ?? '—'}</span>
        </button>
        {available.map((t) => {
          const on = types.includes(t);
          return (
            <button
              key={t}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(t)}
              className={cn('inline-flex h-[28px] items-center gap-1.5 rounded-pill border px-2.5 text-meta font-medium',
                on ? 'border-accent/40 bg-accent-soft text-accent' : 'border-border-2 bg-surface text-fg-2 hover:text-fg')}
            >
              {CHANGE_LABEL[t]} <span className="num">{counts[t] ?? 0}</span>
            </button>
          );
        })}
      </div>

      {list.isError && list.data && <StaleDataBanner error={list.error} onRetry={list.refetch} updatedAt={list.dataUpdatedAt} />}
      <Card padding="none" as="section" aria-label="Change feed">
        {list.isLoading ? (
          <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
          </div>
        ) : list.isError && !list.data ? (
          <QueryErrorState error={list.error} onRetry={list.refetch} title="Could not load the change feed" />
        ) : groups.length === 0 ? (
          <EmptyState
            variant={types.length ? 'no-results' : 'confirmed'}
            title={types.length ? 'No changes of this type' : 'Nothing changed in this window'}
            asOf={formatClock(until)}
            action={types.length ? (
              <button type="button" onClick={() => setTypes([])} className="text-ui font-medium text-accent hover:text-accent-hover">Show all types</button>
            ) : undefined}
          />
        ) : (
          <>
            {groups.map((g) => (
              <div key={g.day}>
                <h2 className="sticky top-[60px] z-[1] border-b border-border bg-surface px-5 py-2 text-meta font-medium text-fg-2 max-[759px]:px-4">{g.day}</h2>
                <ul>{g.items.map((it, i) => <ChangeRow key={`${it.type}-${it.object.id}-${it.at}-${i}`} item={it} />)}</ul>
              </div>
            ))}
            <Pagination page={page} pageSize={PAGE_SIZE} total={list.data?.total ?? 0} onPageChange={setPage} />
          </>
        )}
      </Card>
      <p className="mt-3 text-meta text-fg-3">
        Not recorded anywhere and therefore not listed: up/down flips of single hosts outside incidents, integration errors and recoveries, and who viewed what. Configuration changes are visible to admins only.
      </p>
    </div>
  );
}

export default function ChangesPage() {
  useEffect(() => { document.title = 'Changes | Nodeglow'; }, []);
  return (
    <Suspense fallback={null}>
      <ChangesView />
    </Suspense>
  );
}
