'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Pencil, Plus, Trash2 } from 'lucide-react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { QueryErrorState } from '@/components/ui/QueryState';
import { useHosts } from '@/hooks/queries/useHosts';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, del, get, patch, post } from '@/lib/api';
import { WEEKDAYS, formatInZone, knownTimeZones, scheduleLabel, utcToZonedInput } from '@/lib/maintenance';
import { useIsEditor } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import type { MaintenanceWindow } from '@/types';

const inputClass = 'ng-input';
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
  const [saving, setSaving] = useState(false);
  const { data: hosts } = useHosts();
  const zones = useMemo(() => knownTimeZones(), []);

  useEffect(() => {
    if (open) {
      setForm(initial ? formFromWindow(initial) : EMPTY_FORM);
      setError(null);
      setHostSearch('');
    }
  }, [open, initial]);

  const set = <K extends keyof WindowForm>(key: K, v: WindowForm[K]) => setForm((f) => ({ ...f, [key]: v }));
  const toggleDay = (d: number) =>
    set('weekdays', form.weekdays.includes(d) ? form.weekdays.filter((x) => x !== d) : [...form.weekdays, d]);
  const toggleHost = (hid: number) =>
    set('host_ids', form.host_ids.includes(hid) ? form.host_ids.filter((x) => x !== hid) : [...form.host_ids, hid]);

  const filteredHosts = (hosts ?? []).filter((h) => {
    const q = hostSearch.trim().toLowerCase();
    return !q || h.name.toLowerCase().includes(q) || h.hostname.toLowerCase().includes(q);
  });

  async function save() {
    setSaving(true);
    setError(null);
    try {
      if (initial) await patch(`/api/v1/maintenance-windows/${initial.id}`, payload(form));
      else await post('/api/v1/maintenance-windows', payload(form));
      onSaved();
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save the maintenance window'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={initial ? 'Edit maintenance window' : 'New maintenance window'}>
      <div className="space-y-4">
        <div>
          <label htmlFor={`${id}-name`} className="ng-label">Name</label>
          <input id={`${id}-name`} type="text" value={form.name} placeholder="Sunday patching"
            onChange={(e) => set('name', e.target.value)} className={inputClass} />
        </div>

        <div className="flex gap-4" role="radiogroup" aria-label="Schedule type">
          {(['weekly', 'once'] as const).map((k) => (
            <label key={k} className="flex items-center gap-2 text-sm text-slate-300">
              <input type="radio" name={`${id}-kind`} checked={form.kind === k} onChange={() => set('kind', k)} />
              {k === 'weekly' ? 'Weekly' : 'One-off'}
            </label>
          ))}
        </div>

        {form.kind === 'weekly' ? (
          <>
            <div>
              <span className="ng-label">Days</span>
              <div className="flex flex-wrap gap-1">
                {WEEKDAYS.map((label, d) => (
                  <button key={label} type="button" aria-pressed={form.weekdays.includes(d)} onClick={() => toggleDay(d)}
                    className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${form.weekdays.includes(d)
                      ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                      : 'border-white/[0.08] text-slate-400 hover:text-slate-200'}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label htmlFor={`${id}-start`} className="ng-label">Start</label>
                <input id={`${id}-start`} type="time" value={form.start_time}
                  onChange={(e) => set('start_time', e.target.value)} className={inputClass} />
              </div>
              <div>
                <label htmlFor={`${id}-h`} className="ng-label">Hours</label>
                <input id={`${id}-h`} type="number" min={0} max={168} value={form.hours}
                  onChange={(e) => set('hours', e.target.value)} className={inputClass} />
              </div>
              <div>
                <label htmlFor={`${id}-m`} className="ng-label">Minutes</label>
                <input id={`${id}-m`} type="number" min={0} max={59} value={form.minutes}
                  onChange={(e) => set('minutes', e.target.value)} className={inputClass} />
              </div>
            </div>
            <p className="text-xs text-slate-500">
              Windows may run past midnight. The start follows the wall clock across daylight-saving changes.
            </p>
          </>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label htmlFor={`${id}-from`} className="ng-label">Starts</label>
              <input id={`${id}-from`} type="datetime-local" value={form.starts_at}
                onChange={(e) => set('starts_at', e.target.value)} className={inputClass} />
            </div>
            <div>
              <label htmlFor={`${id}-to`} className="ng-label">Ends</label>
              <input id={`${id}-to`} type="datetime-local" value={form.ends_at}
                onChange={(e) => set('ends_at', e.target.value)} className={inputClass} />
            </div>
          </div>
        )}

        <div>
          <label htmlFor={`${id}-tz`} className="ng-label">Time zone</label>
          <input id={`${id}-tz`} type="text" list={`${id}-zones`} value={form.timezone}
            placeholder="Default (from settings)" onChange={(e) => set('timezone', e.target.value)} className={inputClass} />
          <datalist id={`${id}-zones`}>
            {zones.map((z) => <option key={z} value={z} />)}
          </datalist>
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={form.all_hosts} onChange={(e) => set('all_hosts', e.target.checked)} />
            Applies to all hosts
          </label>
          {!form.all_hosts && (
            <div className="mt-2">
              <input type="search" placeholder="Filter hosts…" aria-label="Filter hosts" value={hostSearch}
                onChange={(e) => setHostSearch(e.target.value)} className={inputClass} />
              <div className="mt-2 max-h-48 overflow-y-auto rounded-md border border-white/[0.06] divide-y divide-white/[0.04]">
                {filteredHosts.map((h) => (
                  <label key={h.id} className="flex items-center gap-2 px-3 py-1.5 text-sm text-slate-300">
                    <input type="checkbox" checked={form.host_ids.includes(h.id)} onChange={() => toggleHost(h.id)} />
                    <span className="truncate">{h.name}</span>
                    <span className="ml-auto text-xs text-slate-500 font-mono truncate">{h.hostname}</span>
                  </label>
                ))}
                {filteredHosts.length === 0 && <p className="px-3 py-2 text-xs text-slate-500">No hosts match.</p>}
              </div>
              <p className="mt-1 text-xs text-slate-500">{form.host_ids.length} selected</p>
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
          Enabled
        </label>

        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !form.name.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** Recurring / one-off maintenance windows: list, create, edit, delete. */
export function MaintenanceWindowsPanel() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const canEdit = useIsEditor();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [editing, setEditing] = useState<MaintenanceWindow | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
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
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await del(`/api/v1/maintenance-windows/${w.id}`);
      refresh();
    } catch (e) {
      toast.show(apiErrorMessage(e, 'Could not delete the window'), 'error');
    }
  }

  async function toggle(w: MaintenanceWindow) {
    try {
      await patch(`/api/v1/maintenance-windows/${w.id}`, { enabled: !w.enabled });
      refresh();
    } catch (e) {
      toast.show(apiErrorMessage(e, 'Could not update the window'), 'error');
    }
  }

  const windows = query.data;

  return (
    <section aria-labelledby="maint-windows-heading" className="mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 id="maint-windows-heading" className="text-sm font-semibold text-slate-300 flex items-center gap-2">
          <CalendarClock className="h-4 w-4 text-amber-400" /> Scheduled windows
        </h2>
        {canEdit && (
          <Button size="sm" onClick={() => { setEditing(null); setEditorOpen(true); }}>
            <Plus size={14} /> New window
          </Button>
        )}
      </div>

      {query.isLoading && <GlassCard className="p-4"><Skeleton className="h-5 w-full" /></GlassCard>}
      {query.isError && !windows && (
        <GlassCard><QueryErrorState error={query.error} onRetry={query.refetch} title="Could not load maintenance windows" /></GlassCard>
      )}
      {windows && windows.length === 0 && (
        <GlassCard>
          <EmptyState icon={CalendarClock} title="No scheduled windows"
            description="Recurring or one-off windows put hosts into maintenance automatically — no alerts, no incidents." />
        </GlassCard>
      )}
      {windows && windows.length > 0 && (
        <div className="space-y-2">
          {windows.map((w) => (
            <GlassCard key={w.id} className="p-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    {w.name}
                    {w.active && <Badge variant="severity" severity="warning">Active</Badge>}
                    {!w.enabled && <Badge>Disabled</Badge>}
                  </p>
                  <p className="text-xs text-slate-400">{scheduleLabel(w)}</p>
                  <p className="text-xs text-slate-500">
                    {w.all_hosts ? 'All hosts' : `${w.host_ids.length} host${w.host_ids.length === 1 ? '' : 's'}`}
                    {w.active && w.current && <> · ends {formatInZone(w.current.end, w.timezone)}</>}
                    {!w.active && w.next && <> · next {formatInZone(w.next.start, w.timezone)}</>}
                  </p>
                </div>
                {canEdit && (
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" onClick={() => toggle(w)}>{w.enabled ? 'Disable' : 'Enable'}</Button>
                    <Button variant="ghost" size="sm" aria-label={`Edit ${w.name}`} onClick={() => { setEditing(w); setEditorOpen(true); }}>
                      <Pencil size={14} />
                    </Button>
                    <Button variant="ghost" size="sm" aria-label={`Delete ${w.name}`} onClick={() => remove(w)} className="text-red-400">
                      <Trash2 size={14} />
                    </Button>
                  </div>
                )}
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      <WindowEditor open={editorOpen} onClose={() => setEditorOpen(false)} initial={editing} onSaved={refresh} />
      {ConfirmDialogElement}
    </section>
  );
}
