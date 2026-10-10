'use client';

import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardHeader } from '@/components/ui/Card';
import { Button, IconButton } from '@/components/ui/Button';
import { StatusPill } from '@/components/ui/StatusPill';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { EmptyState } from '@/components/ui/EmptyState';
import { Checkbox, Field, Input, Switch } from '@/components/ui/Field';
import { QueryState, QueryErrorState } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { get, post, del, patch } from '@/lib/api';
import { CalendarClock, Play, Plus, Radar, Search, Trash2 } from 'lucide-react';
import { timeAgo } from '@/lib/utils';

function formatInterval(m: number) {
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}` : `${m}m`;
}

/* ---------- types ---------- */

interface AliveHost {
  ip: string;
  hostname_ptr: string | null;
  is_monitored: boolean;
  host_id: number | null;
}

interface ScanResult {
  alive: AliveHost[];
  total: number;
}

interface Schedule {
  id: number;
  name: string;
  cidr: string;
  interval_m: number;
  auto_add: boolean;
  enabled: boolean;
  last_run: string | null;
}

/* ---------- page ---------- */

export default function ScannerPage() {
  useEffect(() => { document.title = 'Scanner | Nodeglow'; }, []);
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();

  /* --- manual scan state --- */
  const [cidr, setCidr] = useState('');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  /* --- schedule modal state --- */
  const [showAddSchedule, setShowAddSchedule] = useState(false);
  const [schedName, setSchedName] = useState('');
  const [schedCidr, setSchedCidr] = useState('');
  const [schedInterval, setSchedInterval] = useState(60);
  const [schedAutoAdd, setSchedAutoAdd] = useState(false);

  /* --- queries --- */
  const { confirm, ConfirmDialogElement } = useConfirm();
  const schedulesQuery = useQuery<Schedule[]>({
    queryKey: ['scanner-schedules'],
    queryFn: () => get<Schedule[]>('/api/subnet-scanner/page-data'),
  });

  /* --- mutations --- */
  const scanMutation = useMutation({
    mutationFn: (subnet: string) =>
      post<ScanResult>('/api/subnet-scanner/scan', { cidr: subnet }),
    onSuccess: (data) => {
      setScanResult(data);
      setSelected(new Set());
      toast(`Found ${data.alive.length} alive hosts out of ${data.total}`, 'success');
    },
    onError: () => toast('Scan failed', 'error'),
  });

  const addHostsMutation = useMutation({
    mutationFn: (ips: string[]) =>
      post('/api/subnet-scanner/add-hosts', { ips }),
    onSuccess: () => {
      toast('Hosts added to monitoring', 'success');
      setSelected(new Set());
      // re-scan to refresh monitored status
      if (cidr) scanMutation.mutate(cidr);
    },
    onError: () => toast('Failed to add hosts', 'error'),
  });

  const createScheduleMutation = useMutation({
    mutationFn: (body: { cidr: string; name: string; interval_m: number; auto_add: boolean }) =>
      post('/api/subnet-scanner/schedules', body),
    onSuccess: () => {
      toast('Schedule created', 'success');
      qc.invalidateQueries({ queryKey: ['scanner-schedules'] });
      setShowAddSchedule(false);
      resetScheduleForm();
    },
    onError: () => toast('Failed to create schedule', 'error'),
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: (id: number) => del(`/api/subnet-scanner/schedules/${id}`),
    onSuccess: () => {
      toast('Schedule deleted', 'success');
      qc.invalidateQueries({ queryKey: ['scanner-schedules'] });
    },
    onError: () => toast('Failed to delete schedule', 'error'),
  });

  const runNowMutation = useMutation({
    mutationFn: (id: number) => post(`/api/subnet-scanner/schedules/${id}/run`),
    onSuccess: () => toast('Scan triggered', 'success'),
    onError: () => toast('Failed to trigger scan', 'error'),
  });

  const toggleScheduleMutation = useMutation({
    mutationFn: ({ id, field, value }: { id: number; field: string; value: boolean }) =>
      patch(`/api/subnet-scanner/schedules/${id}`, { [field]: value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scanner-schedules'] }),
    onError: () => toast('Failed to update schedule', 'error'),
  });

  /* --- helpers --- */

  const resetScheduleForm = useCallback(() => {
    setSchedName('');
    setSchedCidr('');
    setSchedInterval(60);
    setSchedAutoAdd(false);
  }, []);

  const handleScan = () => {
    if (!cidr.trim()) return;
    scanMutation.mutate(cidr.trim());
  };

  const toggleSelect = (ip: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ip)) next.delete(ip);
      else next.add(ip);
      return next;
    });
  };

  const selectAllUnmonitored = () => {
    if (!scanResult) return;
    const unmonitored = scanResult.alive.filter((h) => !h.is_monitored).map((h) => h.ip);
    setSelected((prev) => {
      const allSelected = unmonitored.every((ip) => prev.has(ip));
      if (allSelected) return new Set();
      return new Set(unmonitored);
    });
  };

  const handleAddSelected = () => {
    const ips = Array.from(selected);
    if (ips.length === 0) return;
    addHostsMutation.mutate(ips);
  };

  const handleCreateSchedule = () => {
    if (!schedName.trim() || !schedCidr.trim()) return;
    createScheduleMutation.mutate({
      name: schedName.trim(),
      cidr: schedCidr.trim(),
      interval_m: schedInterval,
      auto_add: schedAutoAdd,
    });
  };

  const handleDeleteSchedule = async (sched: Schedule) => {
    const ok = await confirm({
      title: 'Delete scheduled scan',
      description: `Delete the scheduled scan "${sched.name}" (${sched.cidr})? Hosts it already added stay monitored.`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (ok) deleteScheduleMutation.mutate(sched.id);
  };

  const closeScheduleModal = () => {
    setShowAddSchedule(false);
    resetScheduleForm();
  };

  const unmonitoredCount = scanResult?.alive.filter((h) => !h.is_monitored).length ?? 0;
  const allUnmonitoredSelected = unmonitoredCount > 0 && selected.size === unmonitoredCount;

  return (
    <div>
      <PageHeader
        title="Scans"
        description="Find live hosts in a subnet and add them to monitoring, once or on a schedule"
        actions={
          <Button variant="secondary" size="sm" onClick={() => setShowAddSchedule(true)}>
            <Plus size={14} aria-hidden="true" />
            Add schedule
          </Button>
        }
      />

      {/* ── Manual Scan ── */}
      <Card as="section" aria-labelledby="scan-manual" className="mb-4">
        <CardHeader
          title={<span className="inline-flex items-center gap-2"><Radar size={15} className="text-fg-3" aria-hidden="true" /> Scan a subnet</span>}
          titleId="scan-manual"
        />
        <form
          className="mb-5 flex flex-wrap items-end gap-3"
          onSubmit={(e) => { e.preventDefault(); handleScan(); }}
        >
          <Field label="Subnet (CIDR)" className="w-full max-w-sm">
            <Input
              type="text"
              value={cidr}
              onChange={(e) => setCidr(e.target.value)}
              placeholder="e.g. 192.0.2.0/24"
              className="font-mono"
              spellCheck={false}
            />
          </Field>
          <Button type="submit" loading={scanMutation.isPending} disabled={!cidr.trim()}>
            {!scanMutation.isPending && <Search size={15} aria-hidden="true" />}
            {scanMutation.isPending ? 'Scanning…' : 'Scan'}
          </Button>
        </form>

        {scanMutation.isPending && (
          <div className="space-y-2" aria-busy="true" aria-label="Scanning">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
          </div>
        )}

        {scanMutation.isError && !scanMutation.isPending && (
          <QueryErrorState compact error={scanMutation.error} title="Scan failed" onRetry={handleScan} />
        )}

        {!scanResult && !scanMutation.isPending && !scanMutation.isError && (
          <p className="text-ui text-fg-2">Enter a subnet and run a scan. Results show which live hosts are not monitored yet.</p>
        )}

        {scanResult && !scanMutation.isPending && (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-meta text-fg-2" role="status">
                <span className="num">{scanResult.alive.length}</span> alive / <span className="num">{scanResult.total}</span> scanned
                {unmonitoredCount > 0 && <> · <span className="num">{unmonitoredCount}</span> not monitored</>}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {unmonitoredCount > 0 && (
                  <Button size="sm" variant="ghost" onClick={selectAllUnmonitored}>
                    {allUnmonitoredSelected ? 'Deselect all' : 'Select all not monitored'}
                  </Button>
                )}
                {selected.size > 0 && (
                  <Button size="sm" onClick={handleAddSelected} loading={addHostsMutation.isPending}>
                    {!addHostsMutation.isPending && <Plus size={14} aria-hidden="true" />}
                    Add {selected.size} selected
                  </Button>
                )}
              </div>
            </div>

            {scanResult.alive.length === 0 ? (
              <EmptyState compact variant="no-results" title="No live hosts found" description="Nothing in this subnet answered. Check the CIDR and that the scanner can reach it." />
            ) : (
              <TableContainer>
                <Table className="min-w-[520px]">
                  <THead>
                    <Tr>
                      <Th className="w-10"><span className="sr-only">Select</span></Th>
                      <Th>IP address</Th>
                      <Th>PTR hostname</Th>
                      <Th>Monitoring</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {scanResult.alive.map((host) => (
                      <Tr key={host.ip} selected={selected.has(host.ip)}>
                        <Td>
                          {!host.is_monitored && (
                            <Checkbox
                              aria-label={`Select ${host.ip}`}
                              checked={selected.has(host.ip)}
                              onChange={() => toggleSelect(host.ip)}
                            />
                          )}
                        </Td>
                        <Td className="font-mono">{host.ip}</Td>
                        <Td muted>{host.hostname_ptr || '—'}</Td>
                        <Td>
                          {host.is_monitored ? (
                            host.host_id != null ? (
                              <Link href={`/hosts/${host.host_id}`} className="text-meta text-accent hover:text-accent-hover">Monitored</Link>
                            ) : (
                              <span className="text-meta text-fg-2">Monitored</span>
                            )
                          ) : (
                            <span className="text-meta text-fg-3">Not monitored</span>
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </TableContainer>
            )}
          </>
        )}
      </Card>

      {/* ── Scheduled Scans ── */}
      <Card as="section" aria-labelledby="scan-scheduled" padding="none">
        <div className="px-5 pt-5 max-[759px]:px-4 max-[759px]:pt-4">
          <CardHeader
            title={<span className="inline-flex items-center gap-2"><CalendarClock size={15} className="text-fg-3" aria-hidden="true" /> Scheduled scans</span>}
            titleId="scan-scheduled"
            className="mb-3"
          />
        </div>
        <QueryState
          query={schedulesQuery}
          errorTitle="Could not load scheduled scans"
          loading={
            <div className="space-y-3 px-5 pb-5" aria-busy="true" aria-label="Loading schedules">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              compact
              icon={CalendarClock}
              title="No scheduled scans"
              description="Schedule a subnet scan to find new devices automatically."
              action={<Button size="sm" variant="secondary" onClick={() => setShowAddSchedule(true)}><Plus size={14} aria-hidden="true" /> Add schedule</Button>}
            />
          }
        >
          {(rows) => (
            <TableContainer>
              <Table className="min-w-[760px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Name</Th>
                    <Th>CIDR</Th>
                    <Th numeric>Interval</Th>
                    <Th>Auto-add</Th>
                    <Th>Last run</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((sched) => (
                    <Tr key={sched.id}>
                      <Td>
                        <Switch
                          checked={sched.enabled}
                          aria-label={`Schedule ${sched.name} enabled`}
                          disabled={toggleScheduleMutation.isPending}
                          onChange={(v) => toggleScheduleMutation.mutate({ id: sched.id, field: 'enabled', value: v })}
                        />
                      </Td>
                      <Td className="font-medium">{sched.name}</Td>
                      <Td className="font-mono text-meta text-fg-2">{sched.cidr}</Td>
                      <Td numeric muted>{formatInterval(sched.interval_m)}</Td>
                      <Td>
                        <Switch
                          checked={sched.auto_add}
                          aria-label={`Automatically add hosts found by ${sched.name}`}
                          disabled={toggleScheduleMutation.isPending}
                          onChange={(v) => toggleScheduleMutation.mutate({ id: sched.id, field: 'auto_add', value: v })}
                        />
                      </Td>
                      <Td className="whitespace-nowrap">
                        {sched.last_run ? (
                          <time dateTime={sched.last_run} title={new Date(sched.last_run).toLocaleString()} className="text-fg-2">{timeAgo(sched.last_run)}</time>
                        ) : sched.enabled ? (
                          <StatusPill status="unknown" size="sm">Never ran</StatusPill>
                        ) : (
                          <span className="text-fg-3">Never</span>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap text-right">
                        <IconButton
                          size="sm"
                          aria-label={`Run ${sched.name} now`}
                          title="Run now"
                          onClick={() => runNowMutation.mutate(sched.id)}
                          disabled={runNowMutation.isPending}
                        >
                          <Play size={14} aria-hidden="true" />
                        </IconButton>
                        <IconButton
                          size="sm"
                          aria-label={`Delete ${sched.name}`}
                          title="Delete"
                          onClick={() => handleDeleteSchedule(sched)}
                          disabled={deleteScheduleMutation.isPending}
                        >
                          <Trash2 size={14} aria-hidden="true" />
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

      {/* ── Add Schedule Modal ── */}
      <Modal
        open={showAddSchedule}
        onClose={closeScheduleModal}
        title="Add scheduled scan"
        footer={
          <>
            <Button variant="secondary" onClick={closeScheduleModal}>Cancel</Button>
            <Button
              type="submit"
              form="schedule-form"
              loading={createScheduleMutation.isPending}
              disabled={!schedName.trim() || !schedCidr.trim()}
            >
              {createScheduleMutation.isPending ? 'Creating…' : 'Create schedule'}
            </Button>
          </>
        }
      >
        <form
          id="schedule-form"
          className="space-y-4"
          onSubmit={(e) => { e.preventDefault(); handleCreateSchedule(); }}
        >
          <Field label="Name" required>
            <Input type="text" value={schedName} onChange={(e) => setSchedName(e.target.value)} placeholder="e.g. Office LAN" />
          </Field>
          <Field label="Subnet (CIDR)" required>
            <Input type="text" value={schedCidr} onChange={(e) => setSchedCidr(e.target.value)} placeholder="e.g. 192.0.2.0/24" className="font-mono" spellCheck={false} />
          </Field>
          <Field label="Interval (minutes)" hint="Minimum 5 minutes.">
            <Input type="number" value={schedInterval} onChange={(e) => setSchedInterval(Number(e.target.value))} min={5} />
          </Field>
          <Checkbox
            checked={schedAutoAdd}
            onChange={(e) => setSchedAutoAdd(e.target.checked)}
            label="Automatically add discovered hosts to monitoring"
          />
        </form>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
