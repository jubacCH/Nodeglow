'use client';

import { useEffect, useState } from 'react';
import {
  AlertTriangle, Download, LogIn, LogOut, Server, Settings, Shield, Upload, Wrench, X, type LucideIcon,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Input, Select } from '@/components/ui/Field';
import { Pagination } from '@/components/ui/Pagination';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { useAudit } from '@/hooks/queries/useAudit';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { cn } from '@/lib/utils';

const ACTIONS: { value: string; label: string; icon: LucideIcon; destructive?: boolean }[] = [
  { value: 'auth.login', label: 'Login', icon: LogIn },
  { value: 'auth.logout', label: 'Logout', icon: LogOut },
  { value: 'settings.update', label: 'Settings update', icon: Settings },
  { value: 'host.create', label: 'Host create', icon: Server },
  { value: 'host.delete', label: 'Host delete', icon: Server, destructive: true },
  { value: 'incident.acknowledge', label: 'Incident acknowledge', icon: AlertTriangle },
  { value: 'incident.resolve', label: 'Incident resolve', icon: AlertTriangle },
  { value: 'backup.export', label: 'Backup export', icon: Download },
  { value: 'backup.restore', label: 'Backup restore', icon: Upload, destructive: true },
  { value: 'maintenance.toggle', label: 'Maintenance toggle', icon: Wrench },
];
const ACTION_BY_VALUE = new Map(ACTIONS.map((a) => [a.value, a]));

const PAGE_SIZE = 50;

function formatTime(ts: string | null) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString();
}

function detailsText(details: Record<string, unknown> | null): string {
  if (!details || Object.keys(details).length === 0) return '';
  return Object.entries(details)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' · ');
}

/** Filters live in the URL so a filtered view can be shared. */
function writeUrl(action: string, user: string, page: number) {
  const url = new URL(window.location.href);
  const set = (k: string, v: string) => (v ? url.searchParams.set(k, v) : url.searchParams.delete(k));
  set('action', action);
  set('user', user);
  set('page', page > 0 ? String(page + 1) : '');
  window.history.replaceState(window.history.state, '', url);
}

export default function AuditLogPage() {
  useEffect(() => { document.title = 'Audit Log | Nodeglow'; }, []);

  const [page, setPage] = useState(0);
  const [filterAction, setFilterAction] = useState('');
  const [filterUser, setFilterUser] = useState('');
  const debouncedUser = useDebouncedValue(filterUser.trim(), 250);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setFilterAction(q.get('action') ?? '');
    setFilterUser(q.get('user') ?? '');
    const p = Number(q.get('page'));
    if (p > 1) setPage(p - 1);
  }, []);

  useEffect(() => { writeUrl(filterAction, debouncedUser, page); }, [filterAction, debouncedUser, page]);

  const query = useAudit({
    action: filterAction || undefined,
    user: debouncedUser || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const { data } = query;
  const filtered = !!filterAction || !!filterUser;

  function resetFilters() {
    setFilterAction('');
    setFilterUser('');
    setPage(0);
  }

  return (
    <div>
      <PageHeader
        title="Audit log"
        description={
          data
            ? `${data.total.toLocaleString()} ${filtered ? 'matching ' : ''}entries · updated ${formatAsOf(query.dataUpdatedAt)}`
            : 'Who changed what, and when'
        }
      />

      <Card padding="none">
        <div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
          <Field label="Action" className="w-full sm:w-56">
            <Select value={filterAction} onChange={(e) => { setFilterAction(e.target.value); setPage(0); }}>
              <option value="">All actions</option>
              {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </Select>
          </Field>
          <Field label="User" className="w-full sm:w-56">
            <Input
              type="search"
              placeholder="Username"
              value={filterUser}
              onChange={(e) => { setFilterUser(e.target.value); setPage(0); }}
            />
          </Field>
          {filtered && (
            <Button variant="ghost" size="md" onClick={resetFilters}>
              <X size={14} aria-hidden="true" /> Reset filters
            </Button>
          )}
        </div>

        <QueryState
          query={query}
          errorTitle="Could not load the audit log"
          isEmpty={(d) => d.logs.length === 0}
          loading={
            <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          }
          empty={
            filtered ? (
              <EmptyState
                variant="no-results"
                icon={Shield}
                title="No entries match the filters"
                action={<Button variant="secondary" size="sm" onClick={resetFilters}>Reset filters</Button>}
              />
            ) : (
              <EmptyState icon={Shield} title="No audit entries yet" description="Logins, setting changes and other admin actions are recorded here." />
            )
          }
        >
          {(d) => (
            <>
              <TableContainer>
                <Table density="compact">
                  <THead>
                    <Tr>
                      <Th className="pl-4">Time</Th>
                      <Th>User</Th>
                      <Th>Action</Th>
                      <Th>Target</Th>
                      <Th>IP address</Th>
                      <Th>Details</Th>
                      <Th numeric className="pr-4">ID</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {d.logs.map((entry) => {
                      const meta = ACTION_BY_VALUE.get(entry.action);
                      const Icon = meta?.icon ?? Shield;
                      const details = detailsText(entry.details);
                      return (
                        <Tr key={entry.id}>
                          <Td muted className="num whitespace-nowrap pl-4 text-meta">{formatTime(entry.timestamp)}</Td>
                          <Td className="whitespace-nowrap">{entry.username ?? <span className="text-fg-3">—</span>}</Td>
                          <Td className="whitespace-nowrap">
                            <span className="inline-flex items-center gap-2">
                              <Icon size={14} aria-hidden="true" className={cn(meta?.destructive ? 'text-down' : 'text-fg-3')} />
                              <code className="font-mono text-meta">{entry.action}</code>
                            </span>
                          </Td>
                          <Td className="max-w-[260px] truncate">
                            {entry.target_name || entry.target_type ? (
                              <span title={[entry.target_type, entry.target_name].filter(Boolean).join('/')}>
                                {entry.target_type && <span className="text-fg-3">{entry.target_type}{entry.target_name ? '/' : ''}</span>}
                                {entry.target_name}
                                {entry.target_id != null && <span className="ml-1 font-mono text-meta text-fg-3">#{entry.target_id}</span>}
                              </span>
                            ) : <span className="text-fg-3">—</span>}
                          </Td>
                          <Td muted className="whitespace-nowrap font-mono text-meta">{entry.ip_address ?? '—'}</Td>
                          <Td muted className="max-w-[280px] truncate font-mono text-meta" title={details || undefined}>
                            {details || '—'}
                          </Td>
                          <Td numeric muted className="pr-4 font-mono text-meta">{entry.id}</Td>
                        </Tr>
                      );
                    })}
                  </TBody>
                </Table>
              </TableContainer>
              <Pagination page={page} pageSize={PAGE_SIZE} total={d.total} onPageChange={setPage} />
            </>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
