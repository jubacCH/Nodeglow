'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronRight, FileText, Pencil, SearchX, X } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { SeverityBadge } from '@/components/syslog/severity';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button, IconButton } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Input } from '@/components/ui/Field';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { Tag } from '@/components/ui/Tag';
import { get, patch } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import type { LogTemplate } from '@/types';

interface TemplatesResponse {
  templates: (LogTemplate & { avg_rate_per_hour?: number })[];
  total: number;
}

interface AftermathPattern {
  template_hash: string;
  example: string;
  frequency: number;
  percentage: number;
  avg_severity: number;
}

interface RootCauseResponse {
  total_count: number;
  hosts_affected: number;
  template: string;
  first_seen: string | null;
  last_seen: string | null;
  aftermath: AftermathPattern[];
  sample_size: number;
}

type SortMode = 'recent' | 'frequent' | 'noisy' | 'newest';

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: 'recent', label: 'Recent' },
  { value: 'frequent', label: 'Most frequent' },
  { value: 'noisy', label: 'Noisiest' },
  { value: 'newest', label: 'Newest' },
];

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Noise score 0–100 as a small neutral meter: noise is not a health state. */
function NoiseMeter({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-fg-2" title="Noise score: how repetitive and low-signal this pattern is (0–100)">
      <span>Noise</span>
      <span aria-hidden="true" className="relative h-[4px] w-[40px] overflow-hidden rounded-full bg-surface-3">
        <span className="absolute inset-y-0 left-0 rounded-full bg-fg-3" style={{ width: `${pct}%` }} />
      </span>
      <span className="num text-fg">{score}</span>
      <span className="sr-only">of 100</span>
    </span>
  );
}

function TagEditor({ templateHash, currentTags, onSaved }: { templateHash: string; currentTags: string; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(currentTags);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  const toast = useToastStore((s) => s.show);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
    else if (wasEditing.current) triggerRef.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

  async function save() {
    setSaving(true);
    try {
      await patch(`/syslog/api/templates/${templateHash}/tags`, { tags: value });
      onSaved();
      setEditing(false);
    } catch {
      toast('Failed to update tags', 'error');
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <Button
        ref={triggerRef}
        variant="ghost"
        size="sm"
        className="h-[22px] px-1.5 text-micro"
        onClick={() => { setEditing(true); setValue(currentTags); }}
      >
        <Pencil size={11} aria-hidden="true" /> {currentTags ? 'Edit tags' : 'Add tags'}
      </Button>
    );
  }

  return (
    <form
      className="flex w-full items-center gap-1.5 sm:w-auto"
      onSubmit={(e) => { e.preventDefault(); save(); }}
    >
      <Input
        ref={inputRef}
        type="text"
        aria-label="Tags, comma separated"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); }}
        placeholder="tag1, tag2, tag3"
        className="h-[28px] min-w-0 flex-1 py-0 text-meta sm:w-[220px]"
      />
      <IconButton type="submit" size="sm" aria-label="Save tags" disabled={saving} aria-busy={saving || undefined}>
        <Check size={14} aria-hidden="true" />
      </IconButton>
      <IconButton size="sm" aria-label="Cancel" onClick={() => setEditing(false)}>
        <X size={14} aria-hidden="true" />
      </IconButton>
    </form>
  );
}

function RootCausePanel({ templateHash }: { templateHash: string }) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['root-cause', templateHash],
    queryFn: () => get<RootCauseResponse>(`/api/root-cause/${templateHash}`),
  });

  if (isLoading) {
    return (
      <div className="space-y-2 py-2" aria-busy="true">
        <p className="text-meta text-fg-3">Analyzing follow-up patterns…</p>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-4/5" />
      </div>
    );
  }

  if (isError && !data) {
    return <QueryErrorState compact error={error} onRetry={refetch} title="Could not load the follow-up analysis" className="items-start text-left" />;
  }

  if (!data?.aftermath?.length) {
    return (
      <p className="py-2 text-meta text-fg-3">
        No follow-up patterns detected for this pattern{data ? ` (${data.total_count.toLocaleString()} occurrences in 30 days)` : ''}.
      </p>
    );
  }

  return (
    <div className="space-y-2 py-1">
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-meta text-fg-3">
        <span><span className="num text-fg-2">{data.total_count.toLocaleString()}</span> occurrences (30 d)</span>
        <span><span className="num text-fg-2">{data.hosts_affected}</span> hosts affected</span>
        {data.last_seen && <span>Last {formatDate(data.last_seen)}</span>}
      </p>
      <h4 className="text-meta font-medium text-fg-2">
        What usually follows <span className="font-normal text-fg-3">({data.sample_size} samples)</span>
      </h4>
      <ul className="space-y-1.5">
        {data.aftermath.map((p) => (
          <li
            key={p.template_hash}
            className="flex flex-col gap-2 rounded-ctl border border-border bg-bg px-3 py-2 sm:flex-row sm:items-start sm:gap-3"
          >
            <p className="min-w-0 flex-1 break-all font-mono text-meta text-fg">{p.example}</p>
            <div className="flex shrink-0 items-center gap-2 text-meta text-fg-2">
              <span className="num">{p.frequency}× ({p.percentage}%)</span>
              <SeverityBadge severity={Math.round(p.avg_severity)} short />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function SyslogTemplatesPage() {
  useEffect(() => { document.title = 'Log patterns | Nodeglow'; }, []);
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState<SortMode>('recent');
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['syslog-templates'],
    queryFn: () => get<TemplatesResponse>('/syslog/api/templates'),
  });

  const templates = useMemo(() => data?.templates ?? [], [data?.templates]);

  const filtered = useMemo(() => {
    let result = templates;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (t) =>
          t.template.toLowerCase().includes(q) ||
          t.example?.toLowerCase().includes(q) ||
          t.tags?.toLowerCase().includes(q),
      );
    }
    const sorted = [...result];
    switch (sortMode) {
      case 'recent':
        sorted.sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime());
        break;
      case 'frequent':
        sorted.sort((a, b) => b.count - a.count);
        break;
      case 'noisy':
        sorted.sort((a, b) => b.noise_score - a.noise_score);
        break;
      case 'newest':
        sorted.sort((a, b) => new Date(b.first_seen).getTime() - new Date(a.first_seen).getTime());
        break;
    }
    return sorted;
  }, [templates, search, sortMode]);

  const avgNoise = templates.length
    ? Math.round(templates.reduce((s, t) => s + t.noise_score, 0) / templates.length)
    : null;
  const total = data?.total ?? templates.length;
  const partial = data !== undefined && total > templates.length;
  const asOf = formatAsOf(dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Log patterns"
        description={
          <>
            Message templates extracted from syslog, with noise score and what usually follows
            {asOf && <> · Updated {asOf}</>}
          </>
        }
      />

      {isError && data && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}

      <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          {isLoading ? <Skeleton className="h-[52px] w-20" /> : (
            <BigNumber size="sm" value={data ? total.toLocaleString() : null} label="Patterns" />
          )}
        </Card>
        <Card>
          {isLoading ? <Skeleton className="h-[52px] w-20" /> : (
            <BigNumber
              size="sm"
              value={avgNoise}
              unit={avgNoise !== null ? '/ 100' : undefined}
              label={partial ? `Avg noise score (${templates.length} loaded)` : 'Avg noise score'}
            />
          )}
        </Card>
      </div>

      <div role="search" aria-label="Filter log patterns" className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field label="Search" className="flex-1">
          <Input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Pattern, example or tag…"
          />
        </Field>
        <div className="min-w-0">
          <span className="ng-label" aria-hidden="true">Sort by</span>
          <div className="overflow-x-auto">
            <SegmentedControl label="Sort patterns by" value={sortMode} onChange={setSortMode} options={SORT_OPTIONS} />
          </div>
        </div>
      </div>

      {partial && (
        <p className="mb-3 text-meta text-fg-3">
          Showing the <span className="num text-fg-2">{templates.length}</span> most recently seen of{' '}
          <span className="num text-fg-2">{total.toLocaleString()}</span> patterns. Search and sorting apply to these.
        </p>
      )}

      {isLoading ? (
        <Card padding="none" aria-busy="true" aria-label="Loading patterns">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="space-y-2.5 border-b border-border px-5 py-4 last:border-b-0">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <div className="flex gap-2">
                <Skeleton className="h-5 w-16" />
                <Skeleton className="h-5 w-24" />
              </div>
            </div>
          ))}
        </Card>
      ) : isError && !data ? (
        <Card>
          <QueryErrorState error={error} onRetry={refetch} title="Could not load log patterns" />
        </Card>
      ) : filtered.length === 0 ? (
        <Card>
          {search ? (
            <EmptyState
              variant="no-results"
              icon={SearchX}
              title="No patterns match your search"
              description="Try a different term or clear the search."
              action={<Button variant="secondary" size="sm" onClick={() => setSearch('')}>Clear search</Button>}
            />
          ) : (
            <EmptyState
              variant="not-configured"
              icon={FileText}
              title="No log patterns extracted yet"
              description="Patterns appear automatically once syslog messages are received and processed."
            />
          )}
        </Card>
      ) : (
        <Card padding="none">
          <ul aria-label="Log patterns" className="divide-y divide-border">
            {filtered.map((t) => {
              const isExpanded = expandedHash === t.template_hash;
              const panelId = `rc-${t.template_hash}`;
              const tags = t.tags ? t.tags.split(',').map((s) => s.trim()).filter(Boolean) : [];
              return (
                <li key={t.template_hash} className="px-5 py-4 max-[759px]:px-4">
                  <p className="break-all font-mono text-ui leading-relaxed text-fg">{t.template}</p>
                  {t.example && (
                    <p className="mt-1 break-all font-mono text-meta text-fg-3">
                      <span className="font-sans text-fg-2">Example </span>{t.example}
                    </p>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                    <span className="text-meta text-fg-2"><span className="num font-medium text-fg">{t.count.toLocaleString()}</span> hits</span>
                    {t.avg_rate_per_hour != null && (
                      <span className="text-meta text-fg-2"><span className="num font-medium text-fg">{t.avg_rate_per_hour.toFixed(1)}</span>/h (24 h)</span>
                    )}
                    <NoiseMeter score={t.noise_score} />
                    <span className="text-meta text-fg-3">First {formatDate(t.first_seen)}</span>
                    <span className="text-meta text-fg-3">Last {formatDate(t.last_seen)}</span>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}
                    <TagEditor
                      templateHash={t.template_hash}
                      currentTags={t.tags || ''}
                      onSaved={() => qc.invalidateQueries({ queryKey: ['syslog-templates'] })}
                    />
                    <span className="flex-1" />
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-expanded={isExpanded}
                      aria-controls={isExpanded ? panelId : undefined}
                      onClick={() => setExpandedHash(isExpanded ? null : t.template_hash)}
                      className="text-accent hover:text-accent-hover"
                    >
                      {isExpanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                      What follows
                    </Button>
                  </div>

                  {isExpanded && (
                    <div id={panelId} className="mt-2 border-l-2 border-accent/40 pl-3">
                      <RootCausePanel templateHash={t.template_hash} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}
