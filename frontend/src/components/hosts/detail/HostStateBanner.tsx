'use client';

import Link from 'next/link';
import { Card } from '@/components/ui/Card';
import { StatusDot } from '@/components/ui/StatusDot';
import { glows, toHealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import {
  HOST_STATE_LABEL, STATE_TEXT_CLASS, formatDateTime, hostStatusProp, observedText, reasonText, type HostState,
} from '../hostState';
import type { ProbeAgent } from '../probes';
import type { HostDetailData } from './types';

/** What the unknown/stale state means, in plain words. */
function unknownExplanation(host: HostDetailData, probe: ProbeAgent | undefined): string {
  if (!host.observed_at) return 'This host has no check result yet. Nothing on this page is a current reading.';
  if (probe?.probe?.stale) return `The probe ${probe.name} has stopped reporting, so nobody is checking this host. Values below are from before ${formatDateTime(host.observed_at)}.`;
  return `No check result since ${formatDateTime(host.observed_at)}. Values below are the last ones received, not current readings.`;
}

/** Big state statement at the top of the detail page: state, reason, freshness, who checks. */
export function HostStateBanner({ host, state, probe }: { host: HostDetailData; state: HostState; probe?: ProbeAgent }) {
  const glow = state === 'disabled' ? null : glows(toHealthState(state));
  const checkedBy = host.probe_id == null ? 'Core (direct)' : probe?.name ?? `Probe #${host.probe_id}`;
  const maintEnd = host.maintenance_window?.ends_at ?? host.maintenance_until;
  return (
    <Card
      as="section"
      aria-label="Current state"
      glow={glow ?? undefined}
      className={cn('mb-5', state === 'unknown' && 'border-dashed border-line')}
    >
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="flex min-w-[260px] flex-1 items-start gap-3">
          <span className="mt-[9px]"><StatusDot status={hostStatusProp(state)} size="lg" label="" /></span>
          <div className="min-w-0">
            <p className={cn('font-display text-h3 font-semibold tracking-[-0.03em]', STATE_TEXT_CLASS[state])}>
              {HOST_STATE_LABEL[state]}
            </p>
            <p className="mt-0.5 text-body text-fg">{reasonText(state, host.state_reason)}</p>
            {state === 'unknown' && <p className="mt-1 text-ui text-fg-2">{unknownExplanation(host, probe)}</p>}
            {state === 'maintenance' && (
              <p className="mt-1 text-ui text-fg-2">
                {host.maintenance_window ? <>Window <Link className="text-accent hover:text-accent-hover" href="/alerts?tab=maintenance">{host.maintenance_window.name}</Link></> : 'Manual maintenance'}
                {maintEnd ? ` until ${formatDateTime(maintEnd)}` : ' with no end set'}. Alerts for this host are muted.
              </p>
            )}
          </div>
        </div>
        <dl className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-1 text-meta max-[480px]:grid-cols-1">
          <div>
            <dt className="text-fg-3">Freshness</dt>
            <dd className={cn('text-fg', state === 'unknown' && 'text-unknown')}>{observedText(state, host.observed_at)}</dd>
          </div>
          <div>
            <dt className="text-fg-3">Checked by</dt>
            <dd className="text-fg">
              {checkedBy}
              {probe?.probe?.stale && <span className="text-unknown"> · silent</span>}
            </dd>
          </div>
        </dl>
      </div>
    </Card>
  );
}
