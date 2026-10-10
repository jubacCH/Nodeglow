'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { Checkbox } from '@/components/ui/Field';
import { Badge } from '@/components/ui/Badge';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import type { HostListItem } from '@/hooks/queries/useHosts';
import { cn, formatLatency, uptimeColor } from '@/lib/utils';
import { HostStatePill } from './HostStatePill';
import {
  normalizeHostState, observedShort, observedText, reasonText, type HostState,
} from './hostState';

export type SortKey = 'state' | 'name' | 'latency' | 'uptime' | 'observed';
export type SortDir = 'asc' | 'desc';

export interface HostRow extends HostListItem {
  /** Normalised unified state. */
  st: HostState;
  /** "Core" or the probe's name. */
  checkedBy: string;
  probeSilent: boolean;
}

/** States that rest on a fresh, real measurement (latency is meaningful). */
const OBSERVED: ReadonlySet<HostState> = new Set(['up', 'degraded', 'warning', 'down']);

export function toRow(h: HostListItem, probeNames: Map<number, { name: string; silent: boolean }>): HostRow {
  const probe = h.probe_id != null ? probeNames.get(h.probe_id) : undefined;
  return {
    ...h,
    st: normalizeHostState(h.state),
    checkedBy: h.probe_id == null ? 'Core' : probe?.name ?? `Probe #${h.probe_id}`,
    probeSilent: !!probe?.silent,
  };
}

function Latency({ row }: { row: HostRow }) {
  if (!OBSERVED.has(row.st) || row.latency_ms == null) return <span className="text-fg-3">—</span>;
  return <span className={row.st === 'degraded' ? 'text-degraded' : undefined}>{formatLatency(row.latency_ms)}</span>;
}

function Uptime({ value, row }: { value: number | null; row: HostRow }) {
  if (value == null) return <span className="text-fg-3">—</span>;
  // Past availability of a host nobody watches right now stays neutral.
  const tone = OBSERVED.has(row.st) ? uptimeColor(value) : 'text-fg-2';
  return <span className={tone}>{value >= 100 ? '100' : value.toFixed(value >= 99 ? 2 : 1)}%</span>;
}

function checkTypes(row: HostRow) {
  return (row.check_type || 'icmp').split(',').map((t) => t.trim()).filter(Boolean);
}

interface ListProps {
  rows: HostRow[];
  selected: Set<number>;
  onToggle: (id: number) => void;
  onToggleAll: () => void;
  allSelected: boolean;
  someSelected: boolean;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  busy?: boolean;
}

/** Desktop table (≥ 760 px). Header sticks under the top bar while the page scrolls. */
export function HostTable({ rows, selected, onToggle, onToggleAll, allSelected, someSelected, sortKey, sortDir, onSort, busy }: ListProps) {
  const sort = (k: SortKey) => (sortKey === k ? sortDir : 'none') as 'asc' | 'desc' | 'none';
  return (
    <TableContainer className="overflow-visible">
      <Table aria-label="Hosts" aria-busy={busy || undefined} className={cn(busy && 'opacity-60')}>
        <THead sticky className="[&_th]:top-topbar">
          <Tr>
            <Th className="w-10 pr-0">
              <Checkbox
                aria-label={allSelected ? 'Deselect all hosts' : 'Select all hosts'}
                checked={allSelected}
                ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                onChange={onToggleAll}
              />
            </Th>
            <Th sort={sort('state')} onSort={() => onSort('state')} className="w-[150px]">State</Th>
            <Th sort={sort('name')} onSort={() => onSort('name')}>Host</Th>
            <Th className="max-[1099px]:hidden">Detail</Th>
            <Th className="max-[999px]:hidden">Checks</Th>
            <Th className="max-[1199px]:hidden">Checked by</Th>
            <Th numeric sort={sort('latency')} onSort={() => onSort('latency')}>Latency</Th>
            <Th numeric sort={sort('uptime')} onSort={() => onSort('uptime')}>24h</Th>
            <Th numeric className="max-[1279px]:hidden">7d</Th>
            <Th numeric className="max-[1279px]:hidden">30d</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((h) => {
            const isSel = selected.has(h.id);
            return (
              <Tr key={h.id} selected={isSel} className={isSel ? undefined : 'hover:bg-surface-2'}>
                <Td className="pr-0">
                  <Checkbox aria-label={`Select ${h.name}`} checked={isSel} onChange={() => onToggle(h.id)} />
                </Td>
                <Td className="py-1.5">
                  <div className="flex flex-col items-start gap-0.5">
                    <HostStatePill state={h.st} reason={h.state_reason} observedAt={h.observed_at} size="sm" />
                    <span className={cn('text-micro', h.st === 'unknown' ? 'text-unknown' : 'text-fg-3')}>
                      {h.st === 'unknown'
                        ? (h.observed_at ? `last data ${observedShort(h.observed_at)}` : 'never observed')
                        : observedShort(h.observed_at)}
                    </span>
                  </div>
                </Td>
                <Td className="max-w-[280px] py-1.5">
                  <Link
                    prefetch={false}
                    href={`/hosts/${h.id}`}
                    className="block truncate font-medium text-fg hover:text-accent"
                  >
                    {h.name}
                  </Link>
                  <span className="block truncate font-mono text-micro text-fg-3">{h.hostname}</span>
                </Td>
                <Td muted className="max-w-[320px] max-[1099px]:hidden">
                  <span className={cn('line-clamp-2 text-meta', h.st === 'up' && 'text-fg-3')}>{reasonText(h.st, h.state_reason)}</span>
                </Td>
                <Td className="max-[999px]:hidden">
                  <div className="flex flex-wrap gap-1">
                    {checkTypes(h).slice(0, 3).map((t) => <Badge key={t}>{t}</Badge>)}
                    {checkTypes(h).length > 3 && <Badge>+{checkTypes(h).length - 3}</Badge>}
                  </div>
                </Td>
                <Td muted className="max-[1199px]:hidden">
                  <span className="inline-flex items-center gap-1.5 text-meta">
                    {h.checkedBy}
                    {h.probeSilent && <span className="text-unknown">(silent)</span>}
                  </span>
                </Td>
                <Td numeric><Latency row={h} /></Td>
                <Td numeric><Uptime value={h.uptime?.h24 ?? null} row={h} /></Td>
                <Td numeric className="max-[1279px]:hidden"><Uptime value={h.uptime?.d7 ?? null} row={h} /></Td>
                <Td numeric className="max-[1279px]:hidden"><Uptime value={h.uptime?.d30 ?? null} row={h} /></Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </TableContainer>
  );
}

/** Phone layout (< 760 px): one card per host, no horizontal scrolling. */
export function HostCards({ rows, selected, onToggle, busy }: Pick<ListProps, 'rows' | 'selected' | 'onToggle' | 'busy'>) {
  return (
    <ul aria-label="Hosts" aria-busy={busy || undefined} className={cn('divide-y divide-border', busy && 'opacity-60')}>
      {rows.map((h) => {
        const isSel = selected.has(h.id);
        return (
          <li key={h.id} className={cn('flex items-start gap-3 px-4 py-3', isSel && 'bg-accent-soft')}>
            <Checkbox className="pt-0.5" aria-label={`Select ${h.name}`} checked={isSel} onChange={() => onToggle(h.id)} />
            <Link prefetch={false} href={`/hosts/${h.id}`} className="group flex min-w-0 flex-1 items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-medium text-fg group-hover:text-accent">{h.name}</span>
                  <HostStatePill state={h.st} size="sm" tooltip={false} className="ml-auto" />
                </div>
                <p className="mt-0.5 truncate font-mono text-micro text-fg-3">{h.hostname}</p>
                {h.st !== 'up' && (
                  <p className="mt-1 line-clamp-2 text-meta text-fg-2">{reasonText(h.st, h.state_reason)}</p>
                )}
                <p className="num mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-meta text-fg-3">
                  <span className={h.st === 'unknown' ? 'text-unknown' : undefined}>{observedText(h.st, h.observed_at)}</span>
                  <span>Latency <Latency row={h} /></span>
                  <span>24h <Uptime value={h.uptime?.h24 ?? null} row={h} /></span>
                  {h.probe_id != null && <span>via {h.checkedBy}{h.probeSilent ? ' (silent)' : ''}</span>}
                </p>
              </div>
              <ChevronRight size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-fg-3" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
