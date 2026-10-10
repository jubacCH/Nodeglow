'use client';

import { Fragment, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ChevronDown, ChevronRight, MapPin, Radio, ScrollText, SearchX } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { SyslogLiveTail } from '@/components/syslog/SyslogLiveTail';
import {
  SEVERITY_FILTER_OPTIONS, SEVERITY_LABELS, SeverityBadge, formatLogTime, hostHref,
} from '@/components/syslog/severity';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { ExportButton } from '@/components/ui/ExportButton';
import { Field, Input, Select } from '@/components/ui/Field';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { TBody, THead, Table, TableContainer, Td, Th, Tr } from '@/components/ui/Table';
import { useSyslog } from '@/hooks/queries/useSyslog';
import type { SyslogMessage } from '@/types';

const LIMIT = 200;

type SortKey = 'timestamp' | 'severity' | 'hostname';
type SortDir = 'asc' | 'desc';

/** Stable row identity, so an expanded row stays open across the 10 s refetch and re-sorting. */
function rowKey(m: SyslogMessage, dup: number) {
  return `${m.timestamp}|${m.hostname}|${m.severity}|${m.template_hash || m.message.slice(0, 64)}|${dup}`;
}

export default function SyslogPage() {
  useEffect(() => { document.title = 'Logs | Nodeglow'; }, []);
  const [search, setSearch] = useState('');
  const [selectedSeverity, setSelectedSeverity] = useState<string | undefined>(undefined);
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [liveEnabled, setLiveEnabled] = useState(false);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('timestamp');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const { data: messages, isLoading, isError, error, refetch, dataUpdatedAt } = useSyslog({
    severity: selectedSeverity,
    limit: LIMIT,
  });

  // Unique hosts for the filter dropdown (from the loaded messages only).
  const hosts = useMemo(() => {
    if (!messages) return [];
    const s = new Set(messages.map((m) => m.hostname));
    return Array.from(s).sort();
  }, [messages]);

  // Key every message once, then filter + sort.
  const keyed = useMemo(() => {
    if (!messages) return [];
    const seen = new Map<string, number>();
    return messages.map((m) => {
      const base = rowKey(m, 0);
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      return { msg: m, key: n === 0 ? base : rowKey(m, n) };
    });
  }, [messages]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    const result = keyed.filter(({ msg: m }) => {
      if (q && !m.message.toLowerCase().includes(q) && !m.hostname.toLowerCase().includes(q)) return false;
      if (selectedHost && m.hostname !== selectedHost) return false;
      return true;
    });
    return [...result].sort((x, y) => {
      const a = x.msg;
      const b = y.msg;
      let cmp = 0;
      if (sortKey === 'timestamp') cmp = a.timestamp.localeCompare(b.timestamp);
      else if (sortKey === 'severity') cmp = a.severity - b.severity;
      else if (sortKey === 'hostname') cmp = a.hostname.localeCompare(b.hostname);
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [keyed, search, selectedHost, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'severity' ? 'asc' : 'desc');
    }
  }

  const sortOf = (key: SortKey) => (sortKey === key ? sortDir : 'none');
  const hasFilters = Boolean(search || selectedSeverity !== undefined || selectedHost);
  const resetFilters = () => { setSearch(''); setSelectedSeverity(undefined); setSelectedHost(''); };
  const asOf = formatAsOf(dataUpdatedAt);
  const loadedCount = messages?.length ?? 0;

  return (
    <div>
      <PageHeader
        title="Logs"
        description={
          <>
            Latest {LIMIT} syslog messages from all sources
            {asOf && <> · Updated {asOf}</>}
          </>
        }
        actions={
          <>
            <Button
              variant={liveEnabled ? 'secondary' : 'ghost'}
              size="sm"
              aria-pressed={liveEnabled}
              onClick={() => setLiveEnabled((v) => !v)}
            >
              <Radio size={14} aria-hidden="true" />
              {liveEnabled ? 'Stop live tail' : 'Live tail'}
            </Button>
            {filtered.length > 0 && (
              <ExportButton
                data={filtered.map(({ msg: m }) => ({
                  timestamp: m.timestamp,
                  severity: SEVERITY_LABELS[m.severity] ?? m.severity,
                  hostname: m.hostname,
                  message: m.message,
                }))}
                filename="syslog"
                columns={[
                  { key: 'timestamp', label: 'Timestamp' },
                  { key: 'severity', label: 'Severity' },
                  { key: 'hostname', label: 'Hostname' },
                  { key: 'message', label: 'Message' },
                ]}
              />
            )}
          </>
        }
      />

      <SyslogLiveTail enabled={liveEnabled} severity={selectedSeverity} />

      {/* Filters */}
      <div role="search" aria-label="Filter log messages" className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_200px_200px]">
        <Field label="Search">
          <Input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Message text or host…"
          />
        </Field>
        <Field label="Host">
          <Select value={selectedHost} onChange={(e) => setSelectedHost(e.target.value)}>
            <option value="">All hosts</option>
            {hosts.map((h) => (
              <option key={h} value={h}>{h}</option>
            ))}
          </Select>
        </Field>
        <Field label="Severity">
          <Select
            value={selectedSeverity ?? ''}
            onChange={(e) => setSelectedSeverity(e.target.value === '' ? undefined : e.target.value)}
          >
            {SEVERITY_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </Field>
      </div>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-meta text-fg-3">
        <p aria-live="polite">
          {messages
            ? <>Showing <span className="num text-fg-2">{filtered.length}</span> of <span className="num text-fg-2">{loadedCount}</span> loaded messages. Search and host filter only cover these {loadedCount}.</>
            : ' '}
        </p>
        {hasFilters && (
          <Button variant="ghost" size="sm" onClick={resetFilters}>Reset filters</Button>
        )}
      </div>

      {isError && messages && <StaleDataBanner error={error} onRetry={refetch} updatedAt={dataUpdatedAt} />}

      <Card padding="none" className="overflow-hidden">
        {!isLoading && isError && !messages ? (
          <QueryErrorState error={error} onRetry={refetch} title="Could not load log messages" />
        ) : !isLoading && filtered.length === 0 ? (
          hasFilters ? (
            <EmptyState
              variant="no-results"
              icon={SearchX}
              title="No messages match these filters"
              description={`Only the latest ${loadedCount} messages are searched. Widen the severity or clear the search.`}
              action={<Button variant="secondary" size="sm" onClick={resetFilters}>Reset filters</Button>}
            />
          ) : (
            <EmptyState
              variant="not-configured"
              icon={ScrollText}
              title="No log messages received yet"
              description="Send syslog from your devices (default UDP 514, TCP 1514) to Nodeglow, or install an agent to collect log files and event logs."
              action={<Link href="/agents" className="text-ui font-medium text-accent hover:text-accent-hover">Set up agents</Link>}
            />
          )
        ) : (
          <TableContainer maxHeight="calc(100vh - 300px)" className="min-h-[240px]">
            <Table density="compact" aria-label="Log messages" aria-busy={isLoading || undefined}>
              <THead sticky>
                <Tr>
                  <Th className="w-[1%]"><span className="sr-only">Details</span></Th>
                  <Th className="w-[1%]" sort={sortOf('timestamp')} onSort={() => toggleSort('timestamp')}>Time</Th>
                  <Th className="w-[1%]" sort={sortOf('severity')} onSort={() => toggleSort('severity')}>Severity</Th>
                  <Th className="w-[1%]" sort={sortOf('hostname')} onSort={() => toggleSort('hostname')}>Host</Th>
                  <Th>Message</Th>
                </Tr>
              </THead>
              <TBody>
                {isLoading &&
                  Array.from({ length: 12 }).map((_, i) => (
                    <Tr key={i}>
                      <Td><Skeleton className="h-3.5 w-3.5" /></Td>
                      <Td><Skeleton className="h-3.5 w-20" /></Td>
                      <Td><Skeleton className="h-3.5 w-14" /></Td>
                      <Td><Skeleton className="h-3.5 w-24" /></Td>
                      <Td><Skeleton className="h-3.5 w-full" /></Td>
                    </Tr>
                  ))}
                {filtered.map(({ msg, key }) => {
                  const fields = msg.extracted_fields ?? {};
                  const fieldKeys = Object.keys(fields);
                  const isExpanded = expandedRow === key;
                  const time = formatLogTime(msg.timestamp);
                  const detailId = `log-detail-${key.replace(/[^a-zA-Z0-9_-]/g, '')}`.slice(0, 120);
                  return (
                    <Fragment key={key}>
                      <Tr selected={isExpanded} className="hover:bg-hover">
                        <Td className="pr-0">
                          <button
                            type="button"
                            aria-expanded={isExpanded}
                            aria-controls={isExpanded ? detailId : undefined}
                            aria-label={isExpanded ? 'Hide message details' : 'Show message details'}
                            onClick={() => setExpandedRow(isExpanded ? null : key)}
                            className="grid h-[22px] w-[22px] place-items-center rounded-ng-sm text-fg-3 hover:bg-surface-2 hover:text-fg"
                          >
                            {isExpanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                          </button>
                        </Td>
                        <Td className="whitespace-nowrap font-mono text-meta text-fg-2">
                          <time dateTime={msg.timestamp} title={time.full}>{time.short}</time>
                        </Td>
                        <Td><SeverityBadge severity={msg.severity} /></Td>
                        <Td className="whitespace-nowrap font-mono text-meta">
                          <Link href={hostHref(msg.host_id, msg.hostname)} className="text-fg-2 hover:text-accent">
                            {msg.hostname || '—'}
                          </Link>
                        </Td>
                        <Td className="w-full max-w-0">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate font-mono text-meta text-fg" title={msg.message}>{msg.message}</span>
                            {fieldKeys.length > 0 && (
                              <span className="shrink-0 rounded-chip bg-surface-2 px-1.5 text-micro text-fg-3">
                                {fieldKeys.length} fields
                              </span>
                            )}
                            {msg.geo_country && (
                              <span className="inline-flex shrink-0 items-center gap-1 rounded-chip bg-surface-2 px-1.5 text-micro text-fg-3">
                                <MapPin size={10} aria-hidden="true" />{msg.geo_country}
                              </span>
                            )}
                          </span>
                        </Td>
                      </Tr>
                      {isExpanded && (
                        <tr id={detailId} className="border-b border-border bg-bg">
                          <td colSpan={5} className="px-4 py-3">
                            <pre className="whitespace-pre-wrap break-words font-mono text-meta leading-relaxed text-fg">{msg.message}</pre>
                            <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1.5 text-meta">
                              <DetailItem term="time" value={time.full} />
                              {msg.app_name && <DetailItem term="app" value={msg.app_name} />}
                              {msg.source_ip && <DetailItem term="source" value={msg.source_ip} />}
                              {fieldKeys.map((k) => <DetailItem key={k} term={k} value={fields[k]} />)}
                              {msg.geo_country && (
                                <DetailItem term="geo" value={`${msg.geo_country}${msg.geo_city ? ` / ${msg.geo_city}` : ''}`} />
                              )}
                            </dl>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </TBody>
            </Table>
          </TableContainer>
        )}
      </Card>
    </div>
  );
}

function DetailItem({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-1.5">
      <dt className="font-medium text-fg-2">{term}</dt>
      <dd className="min-w-0 break-all font-mono text-fg">{value}</dd>
    </div>
  );
}
