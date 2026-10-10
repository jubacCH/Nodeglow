'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardHeader } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Field, Select } from '@/components/ui/Field';
import { StatusPill } from '@/components/ui/StatusPill';
import { apiErrorMessage, patch } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import { probeOptionLabel, type ProbeAgent } from '../probes';
import type { HostDetailData } from './types';

/** Who runs the checks for this host: the core directly or a probe agent. */
export function ProbeCard({ host, probes, className }: { host: HostDetailData; probes: ProbeAgent[]; className?: string }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const current = host.probe_id != null ? String(host.probe_id) : '';
  const [value, setValue] = useState(current);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setValue(current); }, [current]);

  const assigned = probes.find((p) => String(p.id) === current);
  const chosen = probes.find((p) => String(p.id) === value);

  async function save() {
    setSaving(true);
    try {
      await patch(`/api/v1/hosts/${host.id}`, { probe_id: value ? Number(value) : null });
      toast(value ? `${chosen?.name ?? 'Probe'} now checks this host` : 'Core now checks this host', 'success');
      await qc.invalidateQueries({ queryKey: ['host', host.id] });
      qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not change the probe.'), 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card as="section" aria-labelledby="host-probe-title" className={className}>
      <CardHeader
        title="Checked by"
        titleId="host-probe-title"
        actions={
          host.probe_id == null ? null : assigned?.probe?.stale
            ? <StatusPill status="unknown" size="sm">Probe silent</StatusPill>
            : assigned ? <StatusPill status="ok" size="sm">Probe reporting</StatusPill> : <StatusPill status="unknown" size="sm">Probe missing</StatusPill>
        }
      />
      <p className="mb-3 text-ui text-fg-2">
        {host.probe_id == null
          ? 'The Nodeglow core checks this host directly.'
          : assigned
            ? <>The probe <Link className="text-accent hover:text-accent-hover" href={`/agents/${assigned.id}`}>{assigned.name}</Link> checks this host
                {assigned.probe?.last_report ? `; last report ${timeAgo(assigned.probe.last_report)}` : '; it has never reported'}.
                {assigned.probe?.stale && ' While it is silent, this host shows “No data”.'}
              </>
            : `Probe #${host.probe_id} no longer exists, so nobody checks this host.`}
      </p>
      <div className="flex items-end gap-2">
        <Field label="Assign" className="flex-1" hint={probes.length === 0 ? 'No agent is set up as a probe.' : undefined}>
          <Select value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="">Core (direct)</option>
            {probes.map((p) => <option key={p.id} value={p.id}>{probeOptionLabel(p)}</option>)}
          </Select>
        </Field>
        <Button variant="secondary" loading={saving} disabled={value === current} onClick={save} className={probes.length === 0 ? 'mb-[26px]' : undefined}>
          Save
        </Button>
      </div>
    </Card>
  );
}
