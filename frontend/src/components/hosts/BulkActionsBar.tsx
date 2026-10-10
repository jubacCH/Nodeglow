'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Pencil, Power, PowerOff, Radar, Trash2, Wrench, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Field, Input, Select } from '@/components/ui/Field';
import { useConfirm } from '@/hooks/useConfirm';
import { useAgents } from '@/hooks/queries/useAgents';
import { ApiError, apiErrorMessage, del, patch } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import {
  bulkUpdatesFor, describeBulkResult, type BulkAction, type BulkReport, type BulkResponse, type BulkUpdates,
} from './bulk';
import { CHECK_TYPES } from './hostForm';
import { probeOptionLabel, type ProbeAgent } from './probes';

interface BulkActionsBarProps {
  ids: number[];
  /** id → name, to name skipped hosts in the report. */
  names: Map<number, string>;
  onClear: () => void;
  onReport: (report: BulkReport) => void;
}

/** Actions on the selected hosts. Every result names skipped hosts and ignored fields. */
export function BulkActionsBar({ ids, names, onClear, onReport }: BulkActionsBarProps) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [probeOpen, setProbeOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const n = ids.length;

  function finish(report: BulkReport) {
    toast(report.details.length ? `${report.message} ${report.details.join(' ')}` : report.message, report.tone);
    onReport(report);
    qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    qc.invalidateQueries({ queryKey: ['hosts'] });
    if (report.tone !== 'error') onClear();
  }

  async function run(action: Exclude<BulkAction, { kind: 'delete' }>) {
    setBusy(true);
    try {
      const res = await patch<BulkResponse>('/api/v1/hosts/bulk', { ids, updates: bulkUpdatesFor(action) });
      finish(describeBulkResult(action, {
        updated: res?.updated ?? 0, ids: res?.ids ?? [], missing: res?.missing ?? [], ignored_fields: res?.ignored_fields ?? [],
      }, names));
      return true;
    } catch (e) {
      const message = apiErrorMessage(e, 'The bulk change failed.');
      toast(message, 'error');
      onReport({ tone: 'error', message, details: [] });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function runDelete() {
    const ok = await confirm({
      title: `Delete ${n} host${n === 1 ? '' : 's'}?`,
      description: 'Monitoring stops and the hosts are removed from Nodeglow. This cannot be undone.',
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    setBusy(true);
    const deleted: number[] = [];
    const missing: number[] = [];
    const failed: string[] = [];
    for (const id of ids) {
      try {
        await del(`/api/v1/hosts/${id}`);
        deleted.push(id);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) missing.push(id);
        else failed.push(names.get(id) ?? `#${id}`);
      }
    }
    setBusy(false);
    const report = describeBulkResult({ kind: 'delete' }, { updated: deleted.length, ids: deleted, missing, ignored_fields: [] }, names);
    if (failed.length) {
      report.tone = 'error';
      report.details.push(`Could not delete ${failed.slice(0, 5).join(', ')}${failed.length > 5 ? ` and ${failed.length - 5} more` : ''}.`);
    }
    finish(report);
  }

  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-card border border-accent/40 bg-accent-soft px-3 py-2"
    >
      <span className="num mr-1 text-ui font-medium text-fg" aria-live="polite">{n} selected</span>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run({ kind: 'maintenance', on: true })}>
        <Wrench size={13} aria-hidden="true" /> Maintenance on
      </Button>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run({ kind: 'maintenance', on: false })}>
        Maintenance off
      </Button>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => setProbeOpen(true)}>
        <Radar size={13} aria-hidden="true" /> Assign probe
      </Button>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run({ kind: 'enabled', on: true })}>
        <Power size={13} aria-hidden="true" /> Enable
      </Button>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => run({ kind: 'enabled', on: false })}>
        <PowerOff size={13} aria-hidden="true" /> Disable
      </Button>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => setEditOpen(true)}>
        <Pencil size={13} aria-hidden="true" /> Edit
      </Button>
      <Button size="sm" variant="danger" disabled={busy} onClick={runDelete}>
        <Trash2 size={13} aria-hidden="true" /> Delete
      </Button>
      <Button size="sm" variant="ghost" className="ml-auto" onClick={onClear} disabled={busy}>
        <X size={13} aria-hidden="true" /> Clear selection
      </Button>

      <ProbeModal open={probeOpen} count={n} onClose={() => setProbeOpen(false)} onApply={async (probeId, probeName) => {
        if (await run({ kind: 'probe', probeId, probeName })) setProbeOpen(false);
      }} busy={busy} />
      <BulkEditModal open={editOpen} count={n} onClose={() => setEditOpen(false)} onApply={async (updates) => {
        if (await run({ kind: 'edit', updates })) setEditOpen(false);
      }} busy={busy} />
      {ConfirmDialogElement}
    </div>
  );
}

function ProbeModal({ open, count, onClose, onApply, busy }: {
  open: boolean; count: number; busy: boolean;
  onClose: () => void;
  onApply: (probeId: number | null, probeName?: string) => void;
}) {
  const { data: agents } = useAgents();
  const probes = ((agents ?? []) as ProbeAgent[]).filter((a) => a.is_probe);
  const [value, setValue] = useState('');
  const chosen = probes.find((p) => String(p.id) === value);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Assign probe to ${count} host${count === 1 ? '' : 's'}`}
      description="The chosen agent runs the checks for these hosts. Core checks them directly."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button loading={busy} onClick={() => onApply(value ? Number(value) : null, chosen?.name)}>Assign</Button>
        </>
      }
    >
      <Field label="Checked by" hint={probes.length === 0 ? 'No agent is set up as a probe yet.' : undefined}>
        <Select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">Core (direct)</option>
          {probes.map((p) => <option key={p.id} value={p.id}>{probeOptionLabel(p)}</option>)}
        </Select>
      </Field>
      {chosen?.probe?.stale && (
        <p role="status" className="mt-3 rounded-ctl border border-degraded/40 bg-degraded-soft px-3 py-2 text-meta text-degraded">
          This probe is silent. Its hosts will show as “No data” until it reports again.
        </p>
      )}
    </Modal>
  );
}

function BulkEditModal({ open, count, onClose, onApply, busy }: {
  open: boolean; count: number; busy: boolean;
  onClose: () => void;
  onApply: (updates: BulkUpdates) => void;
}) {
  const [checkType, setCheckType] = useState('');
  const [threshold, setThreshold] = useState('');
  const [error, setError] = useState<string | null>(null);

  function apply() {
    const updates: BulkUpdates = {};
    if (checkType) updates.check_type = checkType;
    if (threshold.trim()) {
      if (!/^\d+$/.test(threshold.trim()) || Number(threshold) < 1 || Number(threshold) > 60000) {
        setError('Threshold must be 1–60000 ms.');
        return;
      }
      updates.latency_threshold_ms = Number(threshold);
    }
    if (Object.keys(updates).length === 0) {
      setError('Change at least one field.');
      return;
    }
    setError(null);
    onApply(updates);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Edit ${count} host${count === 1 ? '' : 's'}`}
      description="Empty fields stay unchanged."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button loading={busy} onClick={apply}>Apply</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Check type" hint="Replaces all checks of these hosts.">
          <Select value={checkType} onChange={(e) => setCheckType(e.target.value)}>
            <option value="">No change</option>
            {CHECK_TYPES.filter((c) => c.value !== 'dns').map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </Select>
        </Field>
        <Field label="Latency threshold (ms)" error={error ?? undefined}>
          <Input type="text" inputMode="numeric" placeholder="No change" value={threshold} onChange={(e) => { setThreshold(e.target.value); setError(null); }} />
        </Field>
      </div>
    </Modal>
  );
}
