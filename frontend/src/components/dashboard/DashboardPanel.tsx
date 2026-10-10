'use client';

import Link from 'next/link';
import { useEffect, type ReactNode } from 'react';
import { Info } from 'lucide-react';
import { PanelSection, SidePanel } from '@/components/ui/SidePanel';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { Pill } from '@/components/ui/Tag';
import { buttonClasses } from '@/components/ui/Button';
import { QueryErrorState } from '@/components/ui/QueryState';
import { useHostPreview, useIncidentPreview } from '@/hooks/queries/useDashboard';
import {
  describeSegments, type HealthState,
} from '@/lib/status';
import {
  fmtMs, formatAgo, formatDuration, formatWhen, glyphFor, groupFreshness, hostState, hostStateLabel,
  incidentHostsLabel, severityLabel, severityTone, stateSegments,
} from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { DashboardIncident, DashboardV2, HostGroup } from '@/types/dashboard';
import { useDashboardCtx, type PanelTarget } from './DashboardCard';
import { DeviceGlyph } from './DeviceGlyph';
import { SeverityIcon, IncidentRow } from './IncidentsCard';
import { StateBar } from './GroupsCard';

function GlyphTile({ glyph, state }: { glyph: ReturnType<typeof glyphFor>; state: HealthState }) {
  return (
    <span className={cn('relative grid h-11 w-11 place-items-center rounded-card bg-surface-2', state === 'unknown' ? 'text-fg-3' : 'text-fg')}>
      <DeviceGlyph glyph={glyph} size={22} />
      <span className="absolute -bottom-[3px] -right-[3px] rounded-full bg-surface p-[2px]">
        <StatusDot status={state} size="lg" label="" />
      </span>
    </span>
  );
}

function Stats({ items }: { items: [ReactNode, string][] }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {items.map(([v, k]) => (
        <div key={k} className="min-w-0 rounded-ctl bg-surface-2 px-3 py-2.5">
          <div className="num truncate font-display text-[1.3571rem] font-medium leading-[1.25] tracking-[-0.035em] text-fg">{v}</div>
          <div className="text-micro text-fg-2">{k}</div>
        </div>
      ))}
    </div>
  );
}

function Callout({ children, dashed }: { children: ReactNode; dashed?: boolean }) {
  return (
    <div className={cn('flex items-start gap-2.5 rounded-ctl border border-border-2 p-3 text-ui text-fg', dashed && 'border-dashed')}>
      <Info size={16} className="mt-0.5 shrink-0 text-fg-2" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

function sevPill(severity: string): HealthState {
  const t = severityTone(severity);
  return t === 'crit' ? 'down' : t === 'warn' ? 'warning' : 'unknown';
}

function IncidentPanelBody({ id, item }: { id: number; item: DashboardIncident | undefined }) {
  const { open, now } = useDashboardCtx();
  const q = useIncidentPreview(id);
  const hosts = q.data?.hosts;
  const hostCount = item?.host_count ?? q.data?.host_count ?? null;
  return (
    <>
      <PanelSection>
        <Stats
          items={[
            [hostCount === null ? '—' : hostCount, hostCount === 1 ? 'Affected host' : 'Affected hosts'],
            [item ? formatDuration(item.age_seconds) : q.data ? formatAgo(q.data.created_at, now).replace(' ago', '') : '—', 'Open'],
            [<span key="r" className="font-mono text-ui">{item?.rule ?? q.data?.rule ?? '—'}</span>, 'Rule'],
          ]}
        />
      </PanelSection>
      {item?.summary && (
        <PanelSection title="What is happening">
          <p className="text-ui text-fg">{item.summary}</p>
        </PanelSection>
      )}
      <PanelSection title="Affected hosts">
        {hostCount === null ? (
          <Callout dashed>Affected hosts were not recorded for this incident. This is unknown, not &ldquo;no hosts&rdquo;.</Callout>
        ) : q.isLoading ? (
          <div className="space-y-2"><Skeleton className="h-7 w-full" /><Skeleton className="h-7 w-4/5" /></div>
        ) : q.isError && !hosts ? (
          <QueryErrorState compact error={q.error} onRetry={q.refetch} title="Could not load the affected hosts" />
        ) : hosts && hosts.length > 0 ? (
          <ul>
            {hosts.map((h) => {
              const st = hostState(h.state);
              return (
                <li key={h.id}>
                  <button
                    type="button"
                    onClick={() => open({ kind: 'host', id: h.id, hint: { name: h.name ?? undefined, state: h.state, state_reason: h.state_reason } })}
                    className="-mx-2 grid w-[calc(100%+16px)] grid-cols-[8px_1fr_auto] items-center gap-2.5 rounded-[6px] px-2 py-[7px] text-left text-ui hover:bg-surface-2"
                  >
                    <StatusDot status={st} label="" />
                    <span className="min-w-0">
                      <span className="block truncate text-fg">{h.name ?? `Host ${h.id}`}</span>
                      {h.state_reason && <span className="block truncate text-meta text-fg-3">{h.state_reason}</span>}
                    </span>
                    <span className="text-meta text-fg-2">{hostStateLabel(h.state)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-ui text-fg-2">{incidentHostsLabel(hostCount)}</p>
        )}
      </PanelSection>
    </>
  );
}

function HostPanelBody({ id, incidents }: { id: number; incidents: DashboardIncident[] }) {
  const { open, now } = useDashboardCtx();
  const q = useHostPreview(id);
  const h = q.data;
  const st = h ? hostState(h.state) : null;
  const mine = incidents.filter((i) => i.host_ids?.includes(id));
  return (
    <>
      {q.isLoading ? (
        <PanelSection><div className="space-y-2"><Skeleton className="h-14 w-full" /><Skeleton className="h-5 w-3/4" /></div></PanelSection>
      ) : q.isError && !h ? (
        <PanelSection><QueryErrorState compact error={q.error} onRetry={q.refetch} title="Could not load this host" /></PanelSection>
      ) : h ? (
        <>
          <PanelSection>
            <Stats
              items={[
                [st === 'unknown' || h.latest?.latency_ms == null ? '—' : `${fmtMs(h.latest.latency_ms)} ms`, 'Latency'],
                [<span key="a" className="font-mono text-ui">{h.hostname}</span>, 'Address'],
                [h.observed_at ? formatWhen(h.observed_at, now) : '—', 'Last data'],
              ]}
            />
          </PanelSection>
          <PanelSection title="State">
            {st === 'unknown' ? (
              <Callout dashed>
                {h.state_reason ?? 'No current data.'} This host&rsquo;s state is unknown — it is not shown as up.
                {h.observed_at && <span className="mt-1 block text-meta text-fg-2">Last real check {formatAgo(h.observed_at, now)}.</span>}
              </Callout>
            ) : (
              <p className="text-ui text-fg">
                {h.state_reason ?? hostStateLabel(h.state)}
                {h.observed_at && <span className="mt-1 block text-meta text-fg-3">Observed {formatAgo(h.observed_at, now)} · {h.check_type}</span>}
                {h.state === 'maintenance' && h.maintenance_until && (
                  <span className="mt-1 block text-meta text-fg-3">Maintenance until {formatWhen(h.maintenance_until, now)}</span>
                )}
              </p>
            )}
          </PanelSection>
        </>
      ) : null}
      {mine.length > 0 && (
        <PanelSection title={mine.length === 1 ? 'Incident' : 'Incidents'}>
          {mine.map((i) => <IncidentRow key={i.id} inc={i} onOpen={() => open({ kind: 'incident', id: i.id })} />)}
        </PanelSection>
      )}
    </>
  );
}

function GroupPanelBody({ group: g }: { group: HostGroup }) {
  const { now } = useDashboardCtx();
  const f = groupFreshness(g, now);
  const segs = stateSegments(g.by_state);
  return (
    <>
      <PanelSection>
        <Stats
          items={[
            [g.host_count, g.host_count === 1 ? 'Host' : 'Hosts'],
            [g.kind === 'direct' ? 'Core' : g.fresh === false ? 'Silent' : 'Fresh', 'Checker'],
            [g.kind === 'probe' ? (g.last_report ? formatWhen(g.last_report, now) : 'never') : '—', 'Last report'],
          ]}
        />
      </PanelSection>
      {g.kind === 'probe' && g.fresh === false && (
        <PanelSection>
          <Callout dashed>{f.text}. Its hosts are shown as unknown, not as up, until the probe reports again.</Callout>
        </PanelSection>
      )}
      <PanelSection title="States">
        <StateBar byState={g.by_state} label={describeSegments(segs)} />
        <ul className="mt-3">
          {segs.map((s) => (
            <li key={s.state} className="flex items-center justify-between border-b border-border py-1.5 text-ui last:border-b-0">
              <span className="flex items-center gap-2"><StatusDot status={s.state} label="" glow={false} />{s.label}</span>
              <span className="num font-medium text-fg">{s.count}</span>
            </li>
          ))}
        </ul>
      </PanelSection>
      <PanelSection title="About this group">
        <p className="text-ui text-fg-2">
          {g.description}. Groups follow who checks a host (this instance or a remote probe); they are not sites.
        </p>
      </PanelSection>
    </>
  );
}

/** Right-hand detail panel for an incident, host or group on the dashboard. */
export function DashboardPanel({ target, data, onClose }: { target: PanelTarget | null; data: DashboardV2 | undefined; onClose: () => void }) {
  const items = data?.incidents?.items ?? [];
  const { now } = useDashboardCtx();
  const hostQ = useHostPreview(target?.kind === 'host' ? target.id : null);
  const incQ = useIncidentPreview(target?.kind === 'incident' ? target.id : null);

  // Switching the panel content (incident → host) unmounts the clicked
  // button; keep focus inside the dialog so Escape and the focus trap work.
  const targetKey = target ? `${target.kind}:${target.kind === 'group' ? target.group.id : target.id}` : null;
  useEffect(() => {
    if (!targetKey) return;
    const id = requestAnimationFrame(() => {
      const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]');
      if (dialog && !dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(id);
  }, [targetKey]);

  let title: ReactNode = '';
  let meta: ReactNode = null;
  let icon: ReactNode = null;
  let body: ReactNode = null;
  let footer: ReactNode = null;

  if (target?.kind === 'incident') {
    const item = items.find((i) => i.id === target.id);
    const sev = item?.severity ?? incQ.data?.severity ?? 'info';
    const opened = item?.opened_at ?? incQ.data?.created_at ?? null;
    title = item?.title ?? incQ.data?.title ?? `Incident #${target.id}`;
    icon = <SeverityIcon severity={sev} acknowledged={item?.acknowledged} size="lg" />;
    meta = (
      <>
        <span>Incident #{target.id} · open since {formatWhen(opened, now)}{item ? ` (${formatDuration(item.age_seconds)})` : ''}</span>
        <span className="mt-2 flex flex-wrap gap-1.5">
          <StatusPill status={sevPill(sev)}>{severityLabel(sev)}</StatusPill>
          {item?.acknowledged && <Pill tone="accent">Acknowledged{item.acknowledged_by ? ` by ${item.acknowledged_by}` : ''}</Pill>}
        </span>
      </>
    );
    body = <IncidentPanelBody id={target.id} item={item} />;
    footer = (
      <>
        <Link href={`/incidents/${target.id}`} className={buttonClasses({ variant: 'primary' })}>Open incident timeline</Link>
        <Link href="/alerts" className={buttonClasses({ variant: 'secondary' })}>All incidents</Link>
      </>
    );
  } else if (target?.kind === 'host') {
    const h = hostQ.data;
    const name = h?.name ?? target.hint?.name ?? `Host ${target.id}`;
    const state = h?.state ?? target.hint?.state ?? null;
    const st = hostState(state);
    title = name;
    icon = <GlyphTile glyph={glyphFor(name)} state={st} />;
    meta = (
      <>
        <span>{h ? `${h.check_type.toUpperCase()} check · ${h.source}` : 'Host'}</span>
        <span className="mt-2 flex flex-wrap gap-1.5">
          <StatusPill status={st}>{hostStateLabel(state)}</StatusPill>
        </span>
      </>
    );
    body = <HostPanelBody id={target.id} incidents={items} />;
    footer = <Link href={`/hosts/${target.id}`} className={buttonClasses({ variant: 'primary' })}>Host details</Link>;
  } else if (target?.kind === 'group') {
    const g = target.group;
    const silent = g.kind === 'probe' && g.fresh === false;
    const worst = (['down', 'warning', 'degraded', 'unknown', 'maintenance', 'up'] as const).find((s) => (g.by_state[s] ?? 0) > 0) ?? null;
    title = g.name;
    icon = <GlyphTile glyph={g.kind === 'probe' ? 'probe' : 'server'} state={silent ? 'unknown' : hostState(worst)} />;
    meta = (
      <>
        <span>{g.kind === 'direct' ? 'Checked by this Nodeglow instance' : 'Remote probe'}</span>
        <span className="mt-2 flex flex-wrap gap-1.5">
          {silent ? <StatusPill status="unknown">Probe silent</StatusPill> : g.kind === 'probe' ? <StatusPill status="ok">Reporting</StatusPill> : <Pill>Group</Pill>}
        </span>
      </>
    );
    body = <GroupPanelBody group={g} />;
    footer = g.kind === 'probe' && g.id !== null
      ? <Link href={`/agents/${g.id}`} className={buttonClasses({ variant: 'primary' })}>Open probe</Link>
      : <Link href="/hosts" className={buttonClasses({ variant: 'secondary' })}>All hosts</Link>;
  }

  return (
    <SidePanel open={target !== null} onClose={onClose} title={title} meta={meta} icon={icon} footer={footer}>
      {body}
    </SidePanel>
  );
}
