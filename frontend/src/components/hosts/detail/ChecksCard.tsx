'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, X } from 'lucide-react';
import { Card, CardHeader } from '@/components/ui/Card';
import { Button, IconButton } from '@/components/ui/Button';
import { Field, Input, Switch } from '@/components/ui/Field';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { apiErrorMessage, get, patch } from '@/lib/api';
import { serviceBadge } from '@/lib/agentServices';
import type { HealthState } from '@/lib/status';
import { cn, timeAgo } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import type { AgentServiceState } from '@/types';
import type { HostState } from '../hostState';
import type { HostDetailData } from './types';
import { parseCheckTypes } from './checks';

type CheckStatus = 'off' | 'pending' | 'ok' | 'fail';

function statusOf(key: string, types: string[], detail: Record<string, boolean>): CheckStatus {
  const configured = key.startsWith('tcp:') ? types.includes(key) || types.includes('tcp') : types.includes(key);
  if (!configured) return 'off';
  if (key in detail) return detail[key] ? 'ok' : 'fail';
  return 'pending';
}

/** Dot + text for one check. Results of a host nobody observes are "last result", never green. */
function CheckResult({ status, reason, stale }: { status: CheckStatus; reason?: string; stale: boolean }) {
  if (status === 'off') return <span className="text-meta text-fg-3">Off</span>;
  let dot: HealthState = 'unknown';
  let text = 'Waiting for first result';
  let tone = 'text-fg-3';
  if (status === 'ok') {
    dot = stale ? 'unknown' : 'ok';
    text = stale ? 'Last result OK (not current)' : 'OK';
    tone = stale ? 'text-unknown' : 'text-ok';
  } else if (status === 'fail') {
    dot = stale ? 'unknown' : 'down';
    text = `${stale ? 'Last result failed' : 'Failed'}${reason ? `: ${reason}` : ''}`;
    tone = stale ? 'text-unknown' : 'text-down';
  }
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5 text-meta', tone)}>
      <StatusDot status={dot} size="sm" glow={false} label="" />
      <span className="truncate">{text}</span>
    </span>
  );
}

interface AgentServicesData {
  watched_services?: string[];
  services?: AgentServiceState[];
  services_reported_at?: string | null;
}

const SERVICE_TONE = { ok: 'ok', critical: 'down', warning: 'warning', info: 'unknown', none: 'unknown' } as const;

function AgentServices({ agentId, stale }: { agentId: number; stale: boolean }) {
  const { data } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => get<AgentServicesData>(`/api/v1/agents/${agentId}`),
    refetchInterval: 30_000,
  });
  const services = data?.services ?? [];
  const watched = data?.watched_services ?? [];
  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-ui font-medium text-fg">Agent services</h3>
        <Link href={`/agents/${agentId}`} className="text-meta text-accent hover:text-accent-hover">Edit watched services</Link>
      </div>
      {watched.length === 0 ? (
        <p className="text-meta text-fg-3">No services watched on this agent.</p>
      ) : (
        <ul className="space-y-1.5">
          {(services.length ? services : watched.map((name) => ({ name, state: null }) as unknown as AgentServiceState)).map((s) => {
            const b = serviceBadge(s.state);
            const tone = stale && b.severity === 'ok' ? 'unknown' : SERVICE_TONE[b.severity];
            return (
              <li key={s.name} className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-meta text-fg">{s.name}</span>
                <StatusPill status={tone} size="sm">{stale && b.severity === 'ok' ? 'Last: running' : b.label}</StatusPill>
              </li>
            );
          })}
        </ul>
      )}
      {data?.services_reported_at && (
        <p className="mt-2 text-micro text-fg-3">Reported {timeAgo(data.services_reported_at)}</p>
      )}
    </div>
  );
}

const SIMPLE_CHECKS = [
  { key: 'icmp', label: 'Ping (ICMP)' },
  { key: 'http', label: 'HTTP' },
  { key: 'https', label: 'HTTPS' },
];

/** Configured checks with their latest result; switches apply immediately. */
export function ChecksCard({ host, state, className }: { host: HostDetailData; state: HostState; className?: string }) {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const { types, ports } = parseCheckTypes(host.check_type, host.port);
  // Ping has no entry in check_detail when it is the only check; the latest result is it.
  const detail: Record<string, boolean> = {
    ...(host.latest?.online != null ? { icmp: host.latest.online } : {}),
    ...(host.check_detail ?? {}),
  };
  const reasons = host.check_errors ?? {};
  const stale = state === 'unknown' || state === 'disabled';
  const [port, setPort] = useState('');
  const [portError, setPortError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  async function save(next: string[], what: string) {
    setSaving(what);
    try {
      await patch(`/api/v1/hosts/${host.id}`, { check_type: (next.length ? next : ['icmp']).join(',') });
      await qc.invalidateQueries({ queryKey: ['host', host.id] });
      qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not change the checks.'), 'error');
    } finally {
      setSaving(null);
    }
  }

  function toggle(key: string, on: boolean) {
    const next = new Set(types);
    if (on) next.add(key); else next.delete(key);
    save([...next], key);
  }

  function removePort(p: number) {
    save(types.filter((t) => t !== `tcp:${p}` && t !== 'tcp'), `tcp:${p}`);
  }

  function addPort(e: FormEvent) {
    e.preventDefault();
    const raw = port.trim();
    const p = Number(raw);
    if (!/^\d+$/.test(raw) || p < 1 || p > 65535) { setPortError('Enter a port between 1 and 65535.'); return; }
    if (ports.includes(p)) { setPortError(`TCP ${p} is already checked.`); return; }
    setPortError(null);
    setPort('');
    save([...types.filter((t) => t !== 'tcp'), ...ports.map((x) => `tcp:${x}`).filter((x) => !types.includes(x)), `tcp:${p}`], 'add');
  }

  return (
    <Card as="section" aria-labelledby="host-checks-title" className={className}>
      <CardHeader
        title="Checks"
        titleId="host-checks-title"
        meta={stale ? 'results below are not current' : undefined}
      />
      <ul className="divide-y divide-border">
        {SIMPLE_CHECKS.map(({ key, label }) => {
          const s = statusOf(key, types, detail);
          return (
            <li key={key} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-ui text-fg">{label}</p>
                <CheckResult status={s} reason={reasons[key]} stale={stale} />
              </div>
              <Switch
                aria-label={`${label} check`}
                checked={s !== 'off'}
                disabled={saving !== null}
                onChange={(on) => toggle(key, on)}
              />
            </li>
          );
        })}
        {ports.map((p) => {
          const key = `tcp:${p}`;
          return (
            <li key={key} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-ui text-fg">TCP <span className="font-mono">{p}</span></p>
                <CheckResult status={statusOf(key, types, detail)} reason={reasons[key]} stale={stale} />
              </div>
              <IconButton aria-label={`Remove TCP ${p} check`} size="sm" disabled={saving !== null} onClick={() => removePort(p)}>
                <X size={14} aria-hidden="true" />
              </IconButton>
            </li>
          );
        })}
      </ul>
      <form onSubmit={addPort} className="mt-3 flex items-start gap-2" noValidate>
        <Field label="Add TCP port check" error={portError ?? undefined} className="flex-1">
          <Input
            type="text"
            inputMode="numeric"
            placeholder="22"
            value={port}
            onChange={(e) => { setPort(e.target.value); setPortError(null); }}
          />
        </Field>
        <Button type="submit" variant="secondary" className="mt-[22px]" loading={saving === 'add'} disabled={!port.trim()}>
          <Plus size={14} aria-hidden="true" /> Add
        </Button>
      </form>
      {host.agent && <AgentServices agentId={host.agent.agent_id} stale={stale} />}
    </Card>
  );
}
