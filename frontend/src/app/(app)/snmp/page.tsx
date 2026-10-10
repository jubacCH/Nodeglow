'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Tabs, TabPanel } from '@/components/ui/Tabs';
import { Field, Input, Select, Checkbox, Switch } from '@/components/ui/Field';
import { Table, TableContainer, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { useConfirm } from '@/hooks/useConfirm';
import { useToastStore } from '@/stores/toast';
import { api, get, post, del, patch, ApiError } from '@/lib/api';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import {
  Upload,
  Trash2,
  Search,
  Download,
  Plus,
  Play,
  Database,
  Server,
  BookOpen,
  RefreshCw,
  CheckSquare,
} from 'lucide-react';

/* ---------- types ---------- */

interface Mib {
  id: number;
  name: string;
  oid_count: number;
  uploaded_at?: string;
}

interface HostConfig {
  id: number;
  host_id: number;
  host_name?: string;
  hostname: string;
  credential_id: number | null;
  credential_name?: string;
  port?: number;
  poll_interval: number;
  enabled: boolean;
  preset?: string;
  last_poll?: string | null;
  /** Result of the last poll, when the API provides it. */
  last_ok?: boolean | null;
}

interface AvailableHost {
  id: number;
  name: string;
  hostname: string;
}

interface OidEntry {
  oid: string;
  name: string;
  mib: string;
  syntax?: string;
}

interface LibraryMib {
  name: string;
  description?: string;
  vendor?: string;
}

interface PageData {
  mibs: Mib[];
  host_configs: HostConfig[];
  available_hosts: AvailableHost[];
}

/* ---------- helpers ---------- */

/** Locale date/time, or null for missing/unparsable values (the API may send "None"). */
function formatDate(v: string | null | undefined, mode: 'date' | 'datetime' = 'datetime'): string | null {
  if (!v) return null;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return null;
  return mode === 'date' ? d.toLocaleDateString() : d.toLocaleString();
}

function apiErrorMessage(err: Error, fallback: string): string {
  // ApiError includes the response body in .data
  const apiErr = err as ApiError;
  if (apiErr.data) {
    try {
      return JSON.parse(String(apiErr.data)).error || fallback;
    } catch { /* use default */ }
  }
  return fallback;
}

/**
 * Poll state of a host config. Never polled / no result = unknown, a failed
 * poll = down, disabled = dimmed. OK only for a confirmed successful poll.
 */
function pollState(cfg: HostConfig): { status: HealthState | 'disabled'; label: string } {
  if (!cfg.enabled) return { status: 'disabled', label: 'Disabled' };
  if (cfg.last_ok === true) return { status: 'ok', label: 'OK' };
  if (cfg.last_ok === false) return { status: 'down', label: 'Poll failed' };
  if (!formatDate(cfg.last_poll)) return { status: 'unknown', label: 'Never polled' };
  return { status: 'unknown', label: 'No data' };
}

function TableSkeleton({ rows, cols }: { rows: number; cols: number }) {
  return (
    <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: cols }).map((__, j) => <Skeleton key={j} className="h-5 w-full" />)}
        </div>
      ))}
    </div>
  );
}

/* ---------- tabs ---------- */

type Tab = 'mibs' | 'hosts' | 'oids';

const tabs: { key: Tab; label: string; icon: typeof Database }[] = [
  { key: 'mibs', label: 'MIB library', icon: Database },
  { key: 'hosts', label: 'Host configs', icon: Server },
  { key: 'oids', label: 'OID browser', icon: BookOpen },
];

const TAB_BASE = 'snmp';

/* ---------- page ---------- */

export default function SnmpPage() {
  useEffect(() => { document.title = 'SNMP | Nodeglow'; }, []);
  const [activeTab, setActiveTab] = useState<Tab>('mibs');

  return (
    <div className="min-w-0">
      <PageHeader title="SNMP" description="SNMP monitoring and MIB management" />

      <Tabs
        label="SNMP sections"
        idBase={TAB_BASE}
        value={activeTab}
        onChange={setActiveTab}
        className="mb-4"
        items={tabs.map((t) => {
          const Icon = t.icon;
          return {
            id: t.key,
            label: (
              <span className="inline-flex items-center gap-2">
                <Icon size={15} aria-hidden="true" />
                {t.label}
              </span>
            ),
          };
        })}
      />

      <TabPanel idBase={TAB_BASE} id="mibs" active={activeTab === 'mibs'}><MibLibraryTab /></TabPanel>
      <TabPanel idBase={TAB_BASE} id="hosts" active={activeTab === 'hosts'}><HostConfigsTab /></TabPanel>
      <TabPanel idBase={TAB_BASE} id="oids" active={activeTab === 'oids'}><OidBrowserTab /></TabPanel>
    </div>
  );
}

/* ================================================================
   MIB Library Tab
   ================================================================ */

function MibLibraryTab() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [libraryQuery, setLibraryQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [libraryResults, setLibraryResults] = useState<LibraryMib[]>([]);

  const query = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  /* upload */
  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return api('/api/snmp/mibs/upload', { method: 'POST', body: form });
    },
    onSuccess: () => {
      toast.show('MIB uploaded', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Upload failed', 'error'),
  });

  const handleUpload = useCallback(() => fileRef.current?.click(), []);
  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadMut.mutate(file);
      e.target.value = '';
    },
    [uploadMut],
  );

  /* delete */
  const deleteMut = useMutation({
    mutationFn: (id: number) => del(`/api/snmp/mibs/${id}`),
    onSuccess: () => {
      toast.show('MIB deleted', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Delete failed', 'error'),
  });

  const confirmDelete = async (mib: Mib) => {
    const ok = await confirm({
      title: 'Delete MIB',
      description: `Delete the MIB "${mib.name}"? This cannot be undone.`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (ok) deleteMut.mutate(mib.id);
  };

  /* seed defaults */
  const seedMut = useMutation({
    mutationFn: () => post('/api/snmp/mibs/seed-defaults'),
    onSuccess: () => {
      toast.show('Default MIBs seeded', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Seed failed', 'error'),
  });

  /* library search */
  const searchLibrary = useCallback(async () => {
    if (!libraryQuery.trim()) return;
    setSearching(true);
    try {
      const res = await get<{ results: LibraryMib[] }>(`/api/snmp/mibs/library/search?q=${encodeURIComponent(libraryQuery)}`);
      setLibraryResults(res.results ?? []);
      setSearched(true);
    } catch {
      toast.show('Search failed', 'error');
    } finally {
      setSearching(false);
    }
  }, [libraryQuery, toast]);

  /* library import */
  const importMut = useMutation({
    mutationFn: (mib: LibraryMib) =>
      post('/api/snmp/mibs/library/import', { mib_name: mib.name, vendor: mib.vendor }),
    onSuccess: (_data, mib) => {
      toast.show(`${mib.name} imported`, 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
      qc.invalidateQueries({ queryKey: ['snmp-oids'] });
    },
    onError: (err: Error) => toast.show(apiErrorMessage(err, 'Import failed'), 'error'),
  });

  return (
    <div className="space-y-4">
      {ConfirmDialogElement}

      {/* uploaded MIBs */}
      <Card as="section" padding="none">
        <CardHeader
          title="Uploaded MIBs"
          meta={query.data ? `${query.data.mibs?.length ?? 0}` : undefined}
          className="mb-2 flex-wrap px-4 pt-4"
          actions={
            <>
              <Button size="sm" variant="ghost" onClick={() => seedMut.mutate()} loading={seedMut.isPending}>
                {!seedMut.isPending && <RefreshCw size={14} aria-hidden="true" />}
                Seed defaults
              </Button>
              <Button size="sm" onClick={handleUpload} loading={uploadMut.isPending}>
                {!uploadMut.isPending && <Upload size={14} aria-hidden="true" />}
                Upload MIB
              </Button>
              <input
                ref={fileRef}
                type="file"
                className="hidden"
                accept=".mib,.txt,.my"
                aria-label="MIB file"
                onChange={onFileChange}
              />
            </>
          }
        />

        <QueryState
          query={query}
          compact
          loading={<TableSkeleton rows={4} cols={4} />}
          isEmpty={(d) => (d.mibs ?? []).length === 0}
          empty={
            <EmptyState
              compact
              icon={Database}
              variant="not-configured"
              title="No MIBs uploaded yet"
              description="Upload a MIB file or seed the default MIBs."
            />
          }
        >
          {(d) => (
            <TableContainer>
              <Table className="min-w-[520px]">
                <THead>
                  <Tr>
                    <Th>Name</Th>
                    <Th numeric>OIDs</Th>
                    <Th>Uploaded</Th>
                    <Th className="text-right"><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {(d.mibs ?? []).map((mib) => (
                    <Tr key={mib.id}>
                      <Td className="font-mono text-meta">{mib.name}</Td>
                      <Td numeric>{mib.oid_count ?? '—'}</Td>
                      <Td muted className="whitespace-nowrap text-meta">{formatDate(mib.uploaded_at, 'date') ?? '—'}</Td>
                      <Td className="text-right">
                        <IconButton
                          size="sm"
                          variant="danger"
                          aria-label={`Delete MIB ${mib.name}`}
                          onClick={() => confirmDelete(mib)}
                          disabled={deleteMut.isPending}
                        >
                          <Trash2 size={13} aria-hidden="true" />
                        </IconButton>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>

      {/* online library search */}
      <Card as="section">
        <CardHeader title="Online MIB library" meta="Search and import MIBs from the online library" />
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => { e.preventDefault(); searchLibrary(); }}
          role="search"
        >
          <Field label="Search MIBs" className="min-w-[200px] flex-1">
            <Input
              type="search"
              placeholder="e.g. IF-MIB, HOST-RESOURCES-MIB"
              value={libraryQuery}
              onChange={(e) => setLibraryQuery(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="secondary" loading={searching} disabled={!libraryQuery.trim()}>
            {!searching && <Search size={14} aria-hidden="true" />}
            Search
          </Button>
        </form>

        <div className="mt-3" aria-live="polite">
          {searching && (
            <div className="space-y-2" aria-busy="true" aria-label="Searching">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          )}

          {!searching && libraryResults.length > 0 && (
            <ul className="space-y-1">
              {libraryResults.map((m) => (
                <li
                  key={m.name}
                  className="flex min-w-0 items-center justify-between gap-3 rounded-ctl px-3 py-2 transition-colors hover:bg-surface-2"
                >
                  <div className="min-w-0">
                    <span className="break-all font-mono text-ui text-fg">{m.name}</span>
                    {m.vendor && <Badge className="ml-2">{m.vendor}</Badge>}
                    {m.description && (
                      <p className="mt-0.5 line-clamp-1 text-meta text-fg-3">{m.description}</p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => importMut.mutate(m)}
                    disabled={importMut.isPending}
                    aria-label={`Import ${m.name}`}
                  >
                    <Download size={13} aria-hidden="true" />
                    Import
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {!searching && searched && libraryResults.length === 0 && (
            <EmptyState compact variant="no-results" icon={Search} title="No results" description="Try a different search term." />
          )}
        </div>
      </Card>
    </div>
  );
}

/* ================================================================
   Host Configs Tab
   ================================================================ */

function HostConfigsTab() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [addOpen, setAddOpen] = useState(false);

  const query = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  const availableHosts = query.data?.available_hosts ?? [];

  /* poll now */
  const pollMut = useMutation({
    mutationFn: (configId: number) => post(`/api/snmp/hosts/${configId}/poll`),
    onSuccess: () => toast.show('Poll triggered', 'success'),
    onError: () => toast.show('Poll failed', 'error'),
  });

  /* delete config */
  const deleteMut = useMutation({
    mutationFn: (configId: number) => del(`/api/snmp/hosts/${configId}`),
    onSuccess: () => {
      toast.show('Host config deleted', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Delete failed', 'error'),
  });

  const confirmDelete = async (cfg: HostConfig) => {
    const name = cfg.host_name ?? cfg.hostname;
    const ok = await confirm({
      title: 'Remove SNMP config',
      description: `Stop SNMP polling for "${name}" and delete its SNMP configuration? The host itself is kept.`,
      confirmLabel: 'Remove',
      variant: 'danger',
    });
    if (ok) deleteMut.mutate(cfg.id);
  };

  /* toggle enabled */
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      patch(`/api/snmp/hosts/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['snmp-page'] }),
    onError: () => toast.show('Update failed', 'error'),
  });

  return (
    <>
      {ConfirmDialogElement}
      <Card as="section" padding="none">
        <CardHeader
          title="SNMP-monitored hosts"
          meta={query.data ? `${query.data.host_configs?.length ?? 0}` : undefined}
          className="mb-2 px-4 pt-4"
          actions={
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus size={14} aria-hidden="true" />
              Add host
            </Button>
          }
        />

        <QueryState
          query={query}
          compact
          loading={<TableSkeleton rows={4} cols={6} />}
          isEmpty={(d) => (d.host_configs ?? []).length === 0}
          empty={
            <EmptyState
              compact
              icon={Server}
              variant="not-configured"
              title="No SNMP host configs yet"
              description="Add a host to start polling it via SNMP."
              action={
                <Button size="sm" variant="secondary" onClick={() => setAddOpen(true)}>
                  <Plus size={14} aria-hidden="true" />
                  Add host
                </Button>
              }
            />
          }
        >
          {(d) => (
            <TableContainer>
              <Table className="min-w-[760px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Host</Th>
                    <Th>Credential</Th>
                    <Th numeric>Port</Th>
                    <Th numeric>Interval</Th>
                    <Th>Polling</Th>
                    <Th>Last poll</Th>
                    <Th className="text-right"><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {(d.host_configs ?? []).map((cfg) => {
                    const st = pollState(cfg);
                    const name = cfg.host_name ?? cfg.hostname;
                    return (
                      <Tr key={cfg.id}>
                        <Td className="whitespace-nowrap">
                          <span className="inline-flex items-center gap-1.5">
                            <StatusDot status={st.status} label="" />
                            <span className={cn(st.status === 'unknown' || st.status === 'disabled' ? 'text-fg-3' : 'text-fg', st.status === 'down' && 'text-down')}>
                              {st.label}
                            </span>
                          </span>
                        </Td>
                        <Td className="max-w-[240px]">
                          <p className="truncate font-medium text-fg">{name}</p>
                          {cfg.host_name && cfg.hostname && (
                            <p className="truncate font-mono text-meta text-fg-3">{cfg.hostname}</p>
                          )}
                        </Td>
                        <Td muted className="text-meta">
                          {cfg.credential_name ?? (cfg.credential_id != null ? `#${cfg.credential_id}` : '—')}
                        </Td>
                        <Td numeric muted>{cfg.port ?? '—'}</Td>
                        <Td numeric muted>{cfg.poll_interval != null ? `${cfg.poll_interval} s` : '—'}</Td>
                        <Td>
                          <Switch
                            checked={!!cfg.enabled}
                            onChange={(enabled) => toggleMut.mutate({ id: cfg.id, enabled })}
                            disabled={toggleMut.isPending}
                            aria-label={`SNMP polling for ${name}`}
                          />
                        </Td>
                        <Td muted className="whitespace-nowrap text-meta">{formatDate(cfg.last_poll) ?? '—'}</Td>
                        <Td>
                          <div className="flex items-center justify-end gap-1">
                            <IconButton
                              size="sm"
                              aria-label={`Poll ${name} now`}
                              onClick={() => pollMut.mutate(cfg.id)}
                              disabled={pollMut.isPending}
                            >
                              <Play size={13} aria-hidden="true" />
                            </IconButton>
                            <IconButton
                              size="sm"
                              variant="danger"
                              aria-label={`Remove SNMP config for ${name}`}
                              onClick={() => confirmDelete(cfg)}
                              disabled={deleteMut.isPending}
                            >
                              <Trash2 size={13} aria-hidden="true" />
                            </IconButton>
                          </div>
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>

      <AddHostModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        availableHosts={availableHosts}
      />
    </>
  );
}

/* ---------- Add Host Modal ---------- */

const ADD_HOST_FORM_ID = 'snmp-add-host-form';

function AddHostModal({
  open,
  onClose,
  availableHosts,
}: {
  open: boolean;
  onClose: () => void;
  availableHosts: AvailableHost[];
}) {
  const qc = useQueryClient();
  const toast = useToastStore();

  const [hostId, setHostId] = useState('');
  const [credentialId, setCredentialId] = useState('');
  const [port, setPort] = useState('161');
  const [interval, setInterval] = useState('300');
  const [preset, setPreset] = useState('standard');

  const { data: credData, isError: credError } = useQuery<{ credentials: { id: number; name: string; type: string }[] }>({
    queryKey: ['credentials-list'],
    queryFn: () => get('/api/credentials/list'),
    enabled: open,
  });
  const snmpCreds = (credData?.credentials ?? []).filter((c) =>
    c.type.toLowerCase().includes('snmp'),
  );

  const createMut = useMutation({
    mutationFn: () =>
      post('/api/snmp/hosts', {
        host_id: Number(hostId),
        credential_id: Number(credentialId),
        port: Number(port),
        poll_interval: Number(interval),
        preset,
      }),
    onSuccess: () => {
      toast.show('Host config created', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
      onClose();
      setHostId('');
      setCredentialId('');
      setPort('161');
      setInterval('300');
      setPreset('standard');
    },
    onError: () => toast.show('Failed to create config', 'error'),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add SNMP host"
      footer={
        <>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={ADD_HOST_FORM_ID}
            size="sm"
            loading={createMut.isPending}
            disabled={!hostId || !credentialId}
          >
            {createMut.isPending ? 'Creating…' : 'Create'}
          </Button>
        </>
      }
    >
      <form
        id={ADD_HOST_FORM_ID}
        onSubmit={(e) => {
          e.preventDefault();
          createMut.mutate();
        }}
        className="space-y-4"
      >
        <Field
          label="Host"
          required
          hint={availableHosts.length === 0 ? 'All enabled hosts already have an SNMP config.' : undefined}
        >
          <Select value={hostId} onChange={(e) => setHostId(e.target.value)}>
            <option value="">Select a host…</option>
            {availableHosts.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name} ({h.hostname})
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Credential"
          required
          hint="Community strings and v3 passwords are stored in Credentials and never shown here."
          error={
            credError
              ? 'Credentials could not be loaded.'
              : snmpCreds.length === 0 && credData
                ? 'No SNMP credentials found. Create one under Administration › Credentials first.'
                : undefined
          }
        >
          <Select value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
            <option value="">Select a credential…</option>
            {snmpCreds.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.type})
              </option>
            ))}
          </Select>
        </Field>

        <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2">
          <Field label="Port" required>
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              max={65535}
              placeholder="161"
              value={port}
              onChange={(e) => setPort(e.target.value)}
            />
          </Field>
          <Field label="Poll interval (s)" required>
            <Input
              type="number"
              inputMode="numeric"
              min={10}
              placeholder="300"
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
            />
          </Field>
        </div>

        <Field label="Preset">
          <Select value={preset} onChange={(e) => setPreset(e.target.value)}>
            <option value="standard">Standard (system + interfaces)</option>
            <option value="minimal">Minimal (sysDescr only)</option>
            <option value="full">Full (all common OIDs)</option>
            <option value="custom">Custom OIDs</option>
          </Select>
        </Field>
      </form>
    </Modal>
  );
}

/* ================================================================
   OID Browser Tab
   ================================================================ */

function OidBrowserTab() {
  const toast = useToastStore();
  const qc = useQueryClient();
  const [mibFilter, setMibFilter] = useState('');
  const [keyword, setKeyword] = useState('');
  const [appliedMib, setAppliedMib] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [selectedConfig, setSelectedConfig] = useState('');
  const [testResults, setTestResults] = useState<Record<string, string> | null>(null);
  const [selectedOids, setSelectedOids] = useState<Set<string>>(new Set());

  const queryParams = new URLSearchParams();
  if (appliedMib) queryParams.set('mib', appliedMib);
  if (appliedKeyword) queryParams.set('search', appliedKeyword);
  const qs = queryParams.toString();

  const oidQuery = useQuery<OidEntry[]>({
    queryKey: ['snmp-oids', qs],
    queryFn: () => get<{ oids: OidEntry[] }>(`/api/snmp/oids?${qs}`).then((r) => r.oids),
  });
  const oids = oidQuery.data;
  const isFetching = oidQuery.isFetching;

  const { data: pageData } = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  const mibNames = (pageData?.mibs ?? []).map((m) => m.name);
  const hostConfigs = pageData?.host_configs ?? [];
  const filtered = !!(appliedMib || appliedKeyword);

  const doSearch = () => {
    setAppliedMib(mibFilter);
    setAppliedKeyword(keyword);
    setTestResults(null);
    setSelectedOids(new Set());
  };

  /* toggle OID selection */
  const toggleOid = (oid: string) => {
    setSelectedOids((prev) => {
      const next = new Set(prev);
      if (next.has(oid)) next.delete(oid);
      else next.add(oid);
      return next;
    });
  };

  const toggleAll = () => {
    if (!oids?.length) return;
    if (selectedOids.size === oids.length) {
      setSelectedOids(new Set());
    } else {
      setSelectedOids(new Set(oids.map((o) => o.oid)));
    }
  };

  /* add selected OIDs to host monitoring */
  const addToMonitoringMut = useMutation({
    mutationFn: () =>
      patch(`/api/snmp/hosts/${selectedConfig}`, {
        oids: Array.from(selectedOids),
      }),
    onSuccess: () => {
      toast.show(`${selectedOids.size} OIDs added to monitoring`, 'success');
      setSelectedOids(new Set());
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Failed to update host config', 'error'),
  });

  /* test OIDs against host */
  const testMut = useMutation({
    mutationFn: () => {
      const oidList = (oids ?? []).map((o) => o.oid);
      return post<{ ok: boolean; results: Record<string, string>; error?: string }>(
        `/api/snmp/hosts/${selectedConfig}/test-oids`,
        { oids: oidList },
      );
    },
    onSuccess: (data) => {
      if (data.results && Object.keys(data.results).length > 0) {
        setTestResults(data.results);
        toast.show(`${Object.keys(data.results).length} values returned`, 'success');
      } else {
        setTestResults({});
        toast.show('Host reachable but no values returned', 'warning');
      }
    },
    onError: (err: Error) => toast.show(apiErrorMessage(err, 'SNMP test failed'), 'error'),
  });

  const hasResults = testResults !== null;
  const allSelected = !!oids?.length && selectedOids.size === oids.length;

  /* find value for an OID — exact match or prefix match for walk results */
  const getValue = (oid: string): string | undefined => {
    if (!testResults) return undefined;
    if (testResults[oid] !== undefined) return testResults[oid];
    // Prefix match for table/walk OIDs
    const matches = Object.entries(testResults).filter(([k]) => k.startsWith(oid + '.'));
    if (matches.length === 1) return matches[0][1];
    if (matches.length > 1) return `${matches.length} values`;
    return undefined;
  };

  return (
    <Card as="section" padding="none">
      <CardHeader title="OID browser" meta="Search and explore OIDs from loaded MIBs" className="mb-0 px-4 pt-4" />

      {/* filters */}
      <div className="space-y-3 border-b border-border p-4">
        <form
          role="search"
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => { e.preventDefault(); doSearch(); }}
        >
          <Field label="MIB" className="w-full sm:w-[200px]">
            <Select value={mibFilter} onChange={(e) => setMibFilter(e.target.value)}>
              <option value="">All MIBs</option>
              {mibNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Name or OID" className="min-w-[200px] flex-1">
            <Input
              type="search"
              placeholder="e.g. ifDescr or 1.3.6.1.2.1.2"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="secondary">
            <Search size={14} aria-hidden="true" />
            Search
          </Button>
        </form>

        {/* test against host */}
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Test against host" className="w-full sm:w-[280px]">
            <Select
              value={selectedConfig}
              onChange={(e) => { setSelectedConfig(e.target.value); setTestResults(null); }}
            >
              <option value="">Select host to test…</option>
              {hostConfigs.map((cfg) => (
                <option key={cfg.id} value={cfg.id}>
                  {cfg.host_name ?? cfg.hostname} ({cfg.hostname})
                </option>
              ))}
            </Select>
          </Field>
          <Button
            variant="secondary"
            onClick={() => testMut.mutate()}
            loading={testMut.isPending}
            disabled={!selectedConfig || !oids?.length}
          >
            {!testMut.isPending && <Play size={14} aria-hidden="true" />}
            {testMut.isPending ? 'Testing…' : 'Test'}
          </Button>
          {hasResults && (
            <Button variant="ghost" onClick={() => setTestResults(null)}>
              Clear results
            </Button>
          )}
          {selectedOids.size > 0 && selectedConfig && (
            <Button onClick={() => addToMonitoringMut.mutate()} loading={addToMonitoringMut.isPending}>
              {!addToMonitoringMut.isPending && <CheckSquare size={14} aria-hidden="true" />}
              {addToMonitoringMut.isPending
                ? 'Adding…'
                : `Add ${selectedOids.size} OID${selectedOids.size > 1 ? 's' : ''} to monitoring`}
            </Button>
          )}
          {selectedOids.size > 0 && !selectedConfig && (
            <span className="pb-2 text-meta text-warning">Select a host to add OIDs to monitoring</span>
          )}
        </div>
      </div>

      {/* results */}
      <QueryState
        query={{ ...oidQuery, isLoading: oidQuery.isLoading || (isFetching && !oidQuery.isError) }}
        compact
        loading={<TableSkeleton rows={6} cols={hasResults ? 5 : 4} />}
        empty={
          filtered ? (
            <EmptyState compact variant="no-results" icon={Search} title="No matching OIDs" description="Try another MIB or search term." />
          ) : (
            <EmptyState compact variant="not-configured" icon={BookOpen} title="No OIDs loaded" description="Seed the default MIBs or upload a MIB first." />
          )
        }
      >
        {(list) => (
          <TableContainer>
            <Table className={hasResults ? 'min-w-[860px]' : 'min-w-[640px]'}>
              <THead>
                <Tr>
                  <Th className="w-10">
                    <Checkbox
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label={allSelected ? 'Deselect all OIDs' : 'Select all OIDs'}
                    />
                  </Th>
                  <Th>OID</Th>
                  <Th>Name</Th>
                  <Th>MIB</Th>
                  <Th>Syntax</Th>
                  {hasResults && <Th>Value</Th>}
                </Tr>
              </THead>
              <TBody>
                {list.map((oid, i) => {
                  const val = getValue(oid.oid);
                  const isSelected = selectedOids.has(oid.oid);
                  const cbId = `snmp-oid-${i}`;
                  return (
                    <Tr key={`${oid.oid}-${i}`} selected={isSelected}>
                      <Td>
                        <Checkbox id={cbId} checked={isSelected} onChange={() => toggleOid(oid.oid)} aria-label={`Select ${oid.name || oid.oid}`} />
                      </Td>
                      <Td className="select-all whitespace-nowrap font-mono text-meta text-fg-2">{oid.oid}</Td>
                      <Td>
                        <label htmlFor={cbId} className="cursor-pointer">{oid.name}</label>
                      </Td>
                      <Td>{oid.mib ? <Badge>{oid.mib}</Badge> : '—'}</Td>
                      <Td muted className="text-meta">{oid.syntax ?? '—'}</Td>
                      {hasResults && (
                        <Td className="max-w-[300px] truncate font-mono text-meta" title={val ?? ''}>
                          {val !== undefined ? <span className="text-fg">{val}</span> : <span className="text-fg-3">—</span>}
                        </Td>
                      )}
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
          </TableContainer>
        )}
      </QueryState>
    </Card>
  );
}
