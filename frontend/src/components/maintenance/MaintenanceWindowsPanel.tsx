'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Pencil, Plus, Trash2 } from 'lucide-react';
import { Button, IconButton } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Checkbox, Field, Input, Switch } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusPill } from '@/components/ui/StatusPill';
import { Badge } from '@/components/ui/Badge';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { SegmentedControl } from '@/components/ui/Tabs';
import { useHosts } from '@/hooks/queries/useHosts';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, del, get, patch, post } from '@/lib/api';
import { WEEKDAYS, formatInZone, knownTimeZones, scheduleLabel, utcToZonedInput } from '@/lib/maintenance';
import { cn } from '@/lib/utils';
import { useIsEditor } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import type { MaintenanceWindow } from '@/types';

const QUERY_KEY = ['maintenance-windows'];

interface WindowForm {
  name: string;
  enabled: boolean;
  kind: 'weekly' | 'once';
  weekdays: number[];
  start_time: string;
  hours: string;
  minutes: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  all_hosts: boolean;
  host_ids: number[];
}

type FormErrors = Partial<Record<'name' | 'weekdays' | 'start_time' | 'duration' | 'starts_at' | 'ends_at' | 'hosts', string>>;

const EMPTY_FORM: WindowForm = {
  name: '', enabled: true, kind: 'weekly', weekdays: [6], start_time: '02:00',
  hours: '2', minutes: '0', starts_at: '', ends_at: '', timezone: '',
  all_hosts: false, host_ids: [],
};

function formFromWindow(w: MaintenanceWindow): WindowForm {
  const dur = w.duration_minutes ?? 0;
  return {
    name: w.name, enabled: w.enabled, kind: w.kind, weekdays: w.weekdays,
    start_time: w.start_time ?? '02:00',
    hours: String(Math.floor(dur / 60)), minutes: String(dur % 60),
    starts_at: utcToZonedInput(w.starts_at, w.timezone),
    ends_at: utcToZonedInput(w.ends_at, w.timezone),
    timezone: w.timezone, all_hosts: w.all_hosts, host_ids: w.host_ids,
  };
}

function payload(f: WindowForm) {
  const base = {
    name: f.name.trim(), enabled: f.enabled, kind: f.kind,
    // Empty = the server applies the timezone setting.
    timezone: f.timezone.trim() || undefined,
    all_hosts: f.all_hosts, host_ids: f.all_hosts ? [] : f.host_ids,
  };
  if (f.kind === 'weekly') {
    return {
      ...base, weekdays: f.weekdays, start_time: f.start_time,
      duration_minutes: (Number(f.hours) || 0) * 60 + (Number(f.minutes) || 0),
    };
  }
  // Wall-clock values; the server reads them in the window's time zone.
  return { ...base, starts_at: f.starts_at, ends_at: f.ends_at };
}

/** Client-side checks before saving; the server validates again. */
function validateWindow(f: WindowForm): FormErrors {
  const e: FormErrors = {};
  if (!f.name.trim()) e.name = 'Give the window a name.';
  if (f.kind === 'weekly') {
    if (f.weekdays.length === 0) e.weekdays = 'Pick at least one day.';
    if (!/^\d{2}:\d{2}$/.test(f.start_time)) e.start_time = 'Enter a start time.';
    const h = Number(f.hours);
    const m = Number(f.minutes);
    if (!Number.isFinite(h) || !Number.isFinite(m) || h < 0 || m < 0 || m > 59 || h > 168) e.duration = 'Hours 0–168, minutes 0–59.';
    else if (h * 60 + m <= 0) e.duration = 'The window must last at least one minute.';
  } else {
    if (!f.starts_at) e.starts_at = 'Enter a start.';
    if (!f.ends_at) e.ends_at = 'Enter an end.';
    else if (f.starts_at && f.ends_at <= f.starts_at) e.ends_at = 'The end must be after the start.';
  }
  if (!f.all_hosts && f.host_ids.length === 0) e.hosts = 'Select at least one host, or apply the window to all hosts.';
  return e;
}

function WindowEditor({ open, onClose, initial, onSaved }: {
  open: boolean;
  onClose: () => void;
  initial: MaintenanceWindow | null;
  onSaved: () => void;
}) {
  const id = useId();
  const [form, setForm] = useState<WindowForm>(EMPTY_FORM);
  const [hostSearch, setHostSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const { data: hosts } = useHosts();
  const zones = useMemo(() => knownTimeZones(), []);

  useEffect(() => {
    if (open) {
      setForm(initial ? formFromWindow(initial) : EMPTY_FORM);
      setError(null);
      setSubmitted(false);
      setHostSearch('');
    }
  }, [open, initial]);

  const errors = validateWindow(form);
  const shown: FormErrors = submitted ? errors : {};

  const set = <K extends keyof WindowForm>(key: K, v: WindowForm[K]) => setForm((f) => ({ ...f, [key]: v }));
  const toggleDay = (d: number) =>
    set('weekdays', form.weekdays.includes(d) ? form.weekdays.filter((x) => x !== d) : [...form.weekdays, d].sort());
  const toggleHost = (hid: number) =>
    set('host_ids', form.host_ids.includes(hid) ? form.host_ids.filter((x) => x !== hid) : [...form.host_ids, hid]);

  const filteredHosts = (hosts ?? []).filter((h) => {
    const q = hostSearch.trim().toLowerCase();
    return !q || h.name.toLowerCase().includes(q) || h.hostname.toLowerCase().includes(q);
  });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) return;
    setSaving(true);
    setError(null);
    try {
      if (initial) await patch(`/api/v1/maintenance-windows/${initial.id}`, payload(form));
      else await post('/api/v1/maintenance-windows', payload(form));
      onSaved();
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not save the maintenance window'));
    } finally {
      setSaving(false);
    }
  }

  const formId = `${id}-form`;
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={initial ? 'Edit maintenance window' : 'New maintenance window'}
      description="Hosts in an active window are not alerted and open no incidents."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={formId} loading={saving}>{initial ? 'Save changes' : 'Create window'}</Button>
        </>
      }
    >
      <form id={formId} onSubmit={save} noValidate className="space-y-4">
        <Field label="Name" required error={shown.name}>
          <Input type="text" value={form.name} placeholder="Sunday patching" onChange={(e) => set('name', e.target.value)} />
        </Field>

        <div>
          <span className="ng-label">Schedule</span>
          <SegmentedControl
            label="Schedule type"
            value={form.kind}
            onChange={(k) => set('kind', k)}
            options={[{ value: 'weekly', label: 'Weekly' }, { value: 'once', label: 'One-off' }]}
          />
        </div>

        {form.kind === 'weekly' ? (
          <>
            <fieldset aria-describedby={shown.weekdays ? `${id}-days-err` : undefined}>
              <legend className="ng-label">Days</legend>
              <div className="flex flex-wrap gap-1">
                {WEEKDAYS.map((label, d) => {
                  const on = form.weekdays.includes(d);
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggleDay(d)}
                      className={cn(
                        'h-[30px] min-w-[44px] rounded-ng-sm border px-2.5 text-meta font-medium transition-colors',
                        on ? 'border-accent/40 bg-accent-soft text-accent' : 'border-border-2 text-fg-2 hover:bg-surface-2 hover:text-fg',
                      )}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              {shown.weekdays && <p id={`${id}-days-err`} className="mt-1.5 text-meta text-down">{shown.weekdays}</p>}
            </fieldset>
            <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-3">
              <Field label="Start" required error={shown.start_time}>
                <Input type="time" value={form.start_time} onChange={(e) => set('start_time', e.target.value)} />
              </Field>
              <Field label="Hours" error={shown.duration}>
                <Input type="number" inputMode="numeric" min={0} max={168} value={form.hours} onChange={(e) => set('hours', e.target.value)} />
              </Field>
              <Field label="Minutes">
                <Input type="number" inputMode="numeric" min={0} max={59} value={form.minutes} onChange={(e) => set('minutes', e.target.value)} />
              </Field>
            </div>
            <p className="text-meta text-fg-3">
              Windows may run past midnight. The start follows the wall clock across daylight-saving changes.
            </p>
          </>
        ) : (
          <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2">
            <Field label="Starts" required error={shown.starts_at}>
              <Input type="datetime-local" value={form.starts_at} onChange={(e) => set('starts_at', e.target.value)} />
            </Field>
            <Field label="Ends" required error={shown.ends_at}>
              <Input type="datetime-local" value={form.ends_at} onChange={(e) => set('ends_at', e.target.value)} />
            </Field>
          </div>
        )}

        <Field label="Time zone" hint="Leave empty to use the time zone from Settings.">
          <Input type="text" list={`${id}-zones`} value={form.timezone} placeholder="Default (from settings)" onChange={(e) => set('timezone', e.target.value)} />
        </Field>
        <datalist id={`${id}-zones`}>
          {zones.map((z) => <option key={z} value={z} />)}
        </datalist>

        <fieldset className="space-y-2">
          <legend className="ng-label">Hosts</legend>
          <Checkbox checked={form.all_hosts} onChange={(e) => set('all_hosts', e.target.checked)} label="Applies to all hosts" />
          {!form.all_hosts && (
            <div>
              <Input type="search" placeholder="Filter hosts…" aria-label="Filter hosts" value={hostSearch} onChange={(e) => setHostSearch(e.target.value)} />
              <div className="mt-2 max-h-48 divide-y divide-border overflow-y-auto rounded-ctl border border-border">
                {filteredHosts.map((h) => (
                  <Checkbox
                    key={h.id}
                    className="px-3 py-2"
                    checked={form.host_ids.includes(h.id)}
                    onChange={() => toggleHost(h.id)}
                    label={<span className="flex min-w-0 gap-2"><span className="truncate">{h.name}</span><span className="truncate font-mono text-meta text-fg-3">{h.hostname}</span></span>}
                  />
                ))}
                {filteredHosts.length === 0 && <p className="px-3 py-2 text-meta text-fg-3">{hosts ? 'No hosts match.' : 'Loading hosts…'}</p>}
              </div>
              <p className={cn('mt-1.5 text-meta', shown.hosts ? 'text-down' : 'text-fg-3')} role={shown.hosts ? 'alert' : undefined}>
                {shown.hosts ?? `${form.host_ids.length} selected`}
              </p>
            </div>
          )}
        </fieldset>

        <Checkbox checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} label="Enabled" description="Disabled windows are kept but never start." />

        {error && <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{error}</p>}
      </form>
    </Modal>
  );
}

function WindowState({ w }: { w: MaintenanceWindow }) {
  if (!w.enabled) return <Badge>Disabled</Badge>;
  if (w.active) return <StatusPill status="maint">Active</StatusPill>;
  return <span className="text-meta text-fg-2">Scheduled</span>;
}

function hostsText(w: MaintenanceWindow) {
  return w.all_hosts ? 'All hosts' : `${w.host_ids.length} host${w.host_ids.length === 1 ? '' : 's'}`;
}

function whenText(w: MaintenanceWindow) {
  if (w.active && w.current) return `Ends ${formatInZone(w.current.end, w.timezone)}`;
  if (w.enabled && w.next) return `Next ${formatInZone(w.next.start, w.timezone)}`;
  return '—';
}

/** Recurring / one-off maintenance windows: list, create, edit, delete. */
export function MaintenanceWindowsPanel() {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const canEdit = useIsEditor();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [editing, setEditing] = useState<MaintenanceWindow | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => get<MaintenanceWindow[]>('/api/v1/maintenance-windows'),
    refetchInterval: 60_000,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: QUERY_KEY });
    qc.invalidateQueries({ queryKey: ['maintenance-hosts'] });
    qc.invalidateQueries({ queryKey: ['hosts'] });
  };

  async function remove(w: MaintenanceWindow) {
    const ok = await confirm({
      title: 'Delete maintenance window',
      description: `Delete "${w.name}"? Hosts it covers leave maintenance immediately.`,
      confirmLabel: 'Delete window',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/v1/maintenance-windows/${w.id}`);
      toast(`Deleted "${w.name}"`, 'success');
      refresh();
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not delete the window'), 'error');
    }
  }

  async function toggle(w: MaintenanceWindow) {
    setBusyId(w.id);
    // Optimistic: flip the switch now, roll back on failure.
    qc.setQueryData<MaintenanceWindow[]>(QUERY_KEY, (list) => list?.map((x) => (x.id === w.id ? { ...x, enabled: !w.enabled } : x)));
    try {
      await patch(`/api/v1/maintenance-windows/${w.id}`, { enabled: !w.enabled });
    } catch (e) {
      qc.setQueryData<MaintenanceWindow[]>(QUERY_KEY, (list) => list?.map((x) => (x.id === w.id ? { ...x, enabled: w.enabled } : x)));
      toast(apiErrorMessage(e, 'Could not update the window'), 'error');
    } finally {
      setBusyId(null);
      refresh();
    }
  }

  const openNew = () => { setEditing(null); setEditorOpen(true); };
  const openEdit = (w: MaintenanceWindow) => { setEditing(w); setEditorOpen(true); };

  return (
    <Card as="section" padding="none" aria-labelledby="maint-windows-heading" className="mb-4">
      <div className="px-[20px] pt-[18px] max-[759px]:px-[16px]">
        <CardHeader
          title="Scheduled windows"
          titleId="maint-windows-heading"
          meta={query.data ? `${query.data.length}` : undefined}
          actions={canEdit && (
            <Button size="sm" onClick={openNew}><Plus size={14} aria-hidden="true" /> New window</Button>
          )}
        />
      </div>
      <QueryState
        query={query}
        errorTitle="Could not load maintenance windows"
        compact
        loading={<div className="space-y-2 px-[20px] pb-[20px]" aria-busy="true" aria-label="Loading">{[0, 1].map((i) => <Skeleton key={i} className="h-9 w-full" />)}</div>}
        empty={
          <EmptyState compact icon={CalendarClock} title="No scheduled windows"
            description="Recurring or one-off windows put hosts into maintenance automatically — no alerts, no incidents."
            action={canEdit ? <Button size="sm" variant="secondary" onClick={openNew}><Plus size={14} aria-hidden="true" /> New window</Button> : undefined} />
        }
      >
        {(windows) => (
          <>
            <TableContainer className="max-[759px]:hidden">
              <Table aria-labelledby="maint-windows-heading">
                <THead>
                  <Tr>
                    <Th className="w-[120px] pl-[20px]">State</Th>
                    <Th>Window</Th>
                    <Th className="w-[120px]">Hosts</Th>
                    <Th className="w-[220px]">Next / ends</Th>
                    {canEdit && <Th className="w-[150px]"><span className="sr-only">Actions</span></Th>}
                  </Tr>
                </THead>
                <TBody>
                  {windows.map((w) => (
                    <Tr key={w.id}>
                      <Td className="pl-[20px]"><WindowState w={w} /></Td>
                      <Td className="max-w-0 py-2">
                        <p className="truncate font-medium">{w.name}</p>
                        <p className="truncate text-meta text-fg-3">{scheduleLabel(w)}</p>
                      </Td>
                      <Td muted>{hostsText(w)}</Td>
                      <Td muted>{whenText(w)}</Td>
                      {canEdit && (
                        <Td>
                          <div className="flex items-center justify-end gap-1">
                            <Switch checked={w.enabled} disabled={busyId === w.id} onChange={() => toggle(w)} aria-label={`Enable ${w.name}`} className="mr-2" />
                            <IconButton size="sm" aria-label={`Edit ${w.name}`} onClick={() => openEdit(w)}><Pencil size={14} aria-hidden="true" /></IconButton>
                            <IconButton size="sm" aria-label={`Delete ${w.name}`} onClick={() => remove(w)} className="hover:text-down"><Trash2 size={14} aria-hidden="true" /></IconButton>
                          </div>
                        </Td>
                      )}
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
            <ul className="divide-y divide-border border-t border-border min-[760px]:hidden">
              {windows.map((w) => (
                <li key={w.id} className="px-[16px] py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium text-fg [overflow-wrap:anywhere]">{w.name}</p>
                      <p className="text-meta text-fg-3 [overflow-wrap:anywhere]">{scheduleLabel(w)}</p>
                    </div>
                    <WindowState w={w} />
                  </div>
                  <p className="mt-1 text-meta text-fg-2">{hostsText(w)} · {whenText(w)}</p>
                  {canEdit && (
                    <div className="mt-2 flex items-center gap-1">
                      <Switch checked={w.enabled} disabled={busyId === w.id} onChange={() => toggle(w)} label="Enabled" className="mr-auto" />
                      <IconButton size="sm" aria-label={`Edit ${w.name}`} onClick={() => openEdit(w)}><Pencil size={14} aria-hidden="true" /></IconButton>
                      <IconButton size="sm" aria-label={`Delete ${w.name}`} onClick={() => remove(w)}><Trash2 size={14} aria-hidden="true" /></IconButton>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </QueryState>

      <WindowEditor open={editorOpen} onClose={() => setEditorOpen(false)} initial={editing} onSaved={refresh} />
      {ConfirmDialogElement}
    </Card>
  );
}
