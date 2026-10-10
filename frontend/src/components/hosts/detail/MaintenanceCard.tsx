'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardHeader } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select } from '@/components/ui/Field';
import { StatusPill } from '@/components/ui/StatusPill';
import { apiErrorMessage, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { formatDateTime } from '../hostState';
import type { HostDetailData } from './types';

const DURATIONS = [
  { value: '', label: 'Until I end it' },
  { value: '1h', label: '1 hour' },
  { value: '2h', label: '2 hours' },
  { value: '4h', label: '4 hours' },
  { value: '8h', label: '8 hours' },
  { value: '12h', label: '12 hours' },
  { value: '24h', label: '24 hours' },
  { value: 'custom', label: 'Until a date and time…' },
];

/** Manual maintenance (start/end) and the active window, if any. Maintenance is grey-blue, never amber. */
export function MaintenanceCard({ host, className }: { host: HostDetailData; className?: string }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const [duration, setDuration] = useState('');
  const [until, setUntil] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const manual = host.maintenance_manual ?? host.maintenance ?? false;
  const activeWindow = host.maintenance_window;

  async function submit() {
    const body: Record<string, unknown> = {};
    if (manual) body.action = 'off';
    else if (duration === 'custom') {
      const d = new Date(until);
      if (!until || Number.isNaN(d.getTime())) { setError('Pick a date and time.'); return; }
      if (d.getTime() <= Date.now()) { setError('The end must be in the future.'); return; }
      body.action = 'schedule';
      body.until = d.toISOString();
    } else {
      body.action = 'toggle';
      if (duration) body.duration = duration;
    }
    setError(null);
    setSaving(true);
    try {
      await post(`/api/v1/hosts/${host.id}/maintenance`, body);
      toast(manual ? 'Maintenance ended' : 'Maintenance started', 'success');
      await qc.invalidateQueries({ queryKey: ['host', host.id] });
      qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not change maintenance.'), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card as="section" aria-labelledby="host-maint-title" className={className}>
      <CardHeader
        title="Maintenance"
        titleId="host-maint-title"
        actions={host.maintenance ? <StatusPill status="maint" size="sm">Active</StatusPill> : null}
      />
      {activeWindow && (
        <p className="mb-3 rounded-ctl border border-maint/30 bg-maint-soft px-3 py-2 text-ui text-maint">
          Window <Link href="/alerts?tab=maintenance" className="font-medium underline underline-offset-2">{activeWindow.name}</Link>
          {activeWindow.ends_at ? ` until ${formatDateTime(activeWindow.ends_at)}` : ''}. It ends on its own schedule.
        </p>
      )}
      {manual ? (
        <div className="space-y-3">
          <p className="text-ui text-fg-2">
            Manual maintenance {host.maintenance_until ? `until ${formatDateTime(host.maintenance_until)}` : 'with no end set'}.
            Alerts for this host are muted.
          </p>
          <Button variant="secondary" loading={saving} onClick={submit}>End maintenance</Button>
        </div>
      ) : (
        <div className="space-y-3">
          <Field label="Duration">
            <Select value={duration} onChange={(e) => { setDuration(e.target.value); setError(null); }}>
              {DURATIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </Select>
          </Field>
          {duration === 'custom' && (
            <Field label="End" error={error ?? undefined}>
              <Input type="datetime-local" value={until} onChange={(e) => { setUntil(e.target.value); setError(null); }} />
            </Field>
          )}
          <Button variant="secondary" loading={saving} onClick={submit}>Start maintenance</Button>
        </div>
      )}
    </Card>
  );
}
