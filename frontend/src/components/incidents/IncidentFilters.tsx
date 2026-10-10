'use client';

import { useEffect, useId, useState } from 'react';
import { Search, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Field';
import { cn } from '@/lib/utils';
import {
  INCIDENT_SEVERITIES, INCIDENT_STATUSES, SEVERITY_LABEL, STATUS_LABEL, TIME_RANGES, hasActiveFilters, ruleLabel,
  type IncidentFilters as Filters, type IncidentSeverity, type IncidentStatus, type TimeRange,
} from '@/lib/incidents';

function ToggleChip({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-pill border px-2.5 text-meta font-medium transition-colors',
        pressed
          ? 'border-accent/40 bg-accent-soft text-accent'
          : 'border-border-2 bg-surface text-fg-2 hover:bg-surface-2 hover:text-fg',
      )}
    >
      {children}
    </button>
  );
}

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/** Server-side filters of the incident list. Every change resets to page 1. */
export function IncidentFilters({ value, onChange, rules, onReset }: {
  value: Filters;
  onChange: (next: Filters) => void;
  /** Rule names to offer (known rules plus those seen in the data). */
  rules: string[];
  onReset: () => void;
}) {
  const id = useId();
  const [search, setSearch] = useState(value.search);
  // Follow external changes (back/forward, reset).
  useEffect(() => setSearch(value.search), [value.search]);
  // Debounce typing into the server-side search.
  useEffect(() => {
    if (search === value.search) return;
    const t = setTimeout(() => onChange({ ...value, search, page: 0 }), 350);
    return () => clearTimeout(t);
  }, [search, value, onChange]);

  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch, page: 0 });
  const setStatus = (s: IncidentStatus) => {
    const next = toggle(value.statuses, s);
    // At least one status: an empty selection would silently mean "all".
    if (next.length) set({ statuses: INCIDENT_STATUSES.filter((x) => next.includes(x)) });
  };
  const setSeverity = (s: IncidentSeverity) =>
    set({ severities: INCIDENT_SEVERITIES.filter((x) => toggle(value.severities, s).includes(x)) });

  return (
    <div className="mb-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div role="group" aria-label="Status" className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-meta text-fg-3" aria-hidden="true">Status</span>
          {INCIDENT_STATUSES.map((s) => (
            <ToggleChip key={s} pressed={value.statuses.includes(s)} onClick={() => setStatus(s)}>
              {STATUS_LABEL[s]}
            </ToggleChip>
          ))}
        </div>
        <div role="group" aria-label="Severity (none selected = all)" className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-meta text-fg-3" aria-hidden="true">Severity</span>
          {INCIDENT_SEVERITIES.map((s) => (
            <ToggleChip key={s} pressed={value.severities.includes(s)} onClick={() => setSeverity(s)}>
              {SEVERITY_LABEL[s]}
            </ToggleChip>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 min-[560px]:grid-cols-2 min-[1000px]:flex min-[1000px]:items-center">
        <div className="relative min-w-0 min-[1000px]:w-[280px]">
          <label htmlFor={`${id}-q`} className="sr-only">Search incident titles</label>
          <Search size={14} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-3" />
          <Input
            id={`${id}-q`}
            type="search"
            placeholder="Search titles…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        <div className="min-w-0 min-[1000px]:w-[220px]">
          <label htmlFor={`${id}-rule`} className="sr-only">Rule</label>
          <Select id={`${id}-rule`} value={value.rule} onChange={(e) => set({ rule: e.target.value })}>
            <option value="">All rules</option>
            {rules.map((r) => <option key={r} value={r}>{ruleLabel(r)}</option>)}
          </Select>
        </div>
        <div className="min-w-0 min-[1000px]:w-[180px]">
          <label htmlFor={`${id}-range`} className="sr-only">Opened within</label>
          <Select id={`${id}-range`} value={value.range} onChange={(e) => set({ range: e.target.value as TimeRange })}>
            {TIME_RANGES.map((r) => <option key={r.value} value={r.value}>{r.value === 'all' ? 'Opened any time' : `Opened: ${r.label.toLowerCase()}`}</option>)}
          </Select>
        </div>
        {hasActiveFilters(value) && (
          <Button variant="ghost" size="md" onClick={onReset} className="justify-self-start">
            <X size={14} aria-hidden="true" /> Reset filters
          </Button>
        )}
      </div>
    </div>
  );
}
