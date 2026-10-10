'use client';

import Link from 'next/link';
import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusDot } from '@/components/ui/StatusDot';
import {
  describeProvenance, describeStates, fmtMs, formatDuration, formatWhen, ageSeconds, glyphFor,
  hostState, hostStateLabel, incidentHostsLabel, incidentsForHosts, topologyColumns, wanState,
  type Glyph, type TopoColumn,
} from '@/lib/dashboard';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import type {
  DashboardIncident, HostGroup, InternetSection, SectionError, TopoChild, TopoParent, TopologySection,
} from '@/types/dashboard';
import { DashboardCard, useDashboardCtx } from './DashboardCard';
import { DeviceGlyph } from './DeviceGlyph';

const MAX_CHILDREN = 7;

type Tone = TopoColumn['tone'];

const NODE_TONE: Record<Tone, string> = {
  ok: 'border-border bg-surface-2',
  bad: 'border-down/55 bg-down-soft',
  warn: 'border-warning/45 bg-surface-2',
  unknown: 'border-dashed border-border-2 bg-surface-2',
};

const TRUNK_TONE: Record<Tone, string> = {
  ok: 'border-line',
  bad: 'border-down/60 [box-shadow:var(--ng-branch-glow)]',
  warn: 'border-warning/50',
  unknown: 'border-dashed border-unknown',
};

const TICK_TONE: Record<Tone, string> = {
  ok: 'border-line',
  bad: 'border-down/60',
  warn: 'border-warning/50',
  unknown: 'border-dashed border-unknown',
};

interface NodeProps {
  glyph: Glyph;
  name: string;
  sub: ReactNode;
  state: HealthState;
  tone: Tone;
  glow?: 'crit' | 'warn' | null;
  acknowledged?: boolean;
  onClick?: () => void;
  nodeRef?: (el: HTMLElement | null) => void;
  srState: string;
}

/** Head node (gateway, parent, probe): glyph tile with a state dot. */
function TopoNode({ glyph, name, sub, state, tone, glow, acknowledged, onClick, nodeRef, srState }: NodeProps) {
  return (
    <button
      ref={nodeRef}
      type="button"
      onClick={onClick}
      className={cn(
        'relative z-[1] flex min-w-0 items-center gap-2.5 rounded-[9px] border py-2 pl-2 pr-3 text-left transition-colors hover:border-border-2',
        NODE_TONE[tone],
        glow === 'crit' && 'ng-glow-crit',
        glow === 'warn' && 'ng-glow-warn',
        glow && acknowledged && 'ng-glow-acked',
      )}
    >
      <span className={cn('relative grid h-9 w-9 shrink-0 place-items-center rounded-ctl bg-surface', tone === 'unknown' ? 'text-fg-3' : 'text-fg')}>
        <DeviceGlyph glyph={glyph} className={tone === 'unknown' ? 'opacity-70' : undefined} />
        <span className="absolute -bottom-[3px] -right-[3px] grid place-items-center rounded-full bg-surface-2 p-[2px]">
          <StatusDot status={state} size="lg" label="" glow={state === 'down' || state === 'warning'} />
        </span>
      </span>
      <span className="min-w-0">
        <span className="block truncate text-ui font-medium text-fg">{name}</span>
        <span className="block truncate text-meta text-fg-2">{sub}</span>
      </span>
      <span className="sr-only">, {srState}</span>
    </button>
  );
}

function childValue(c: TopoChild): { text: string; cls: string } {
  switch (c.state) {
    case 'down': return { text: 'Down', cls: 'text-down font-medium' };
    case 'warning': return { text: 'Warning', cls: 'text-warning' };
    case 'degraded': return { text: 'Degraded', cls: 'text-degraded' };
    case 'maintenance': return { text: 'Maintenance', cls: 'text-fg-2' };
    case 'up': return { text: 'Up', cls: 'text-fg-2' };
    default: return { text: 'No data', cls: 'text-fg-3' };
  }
}

function ChildRow({ c, onOpen }: { c: TopoChild; onOpen: () => void }) {
  const v = childValue(c);
  const st = hostState(c.state);
  return (
    <button
      type="button"
      onClick={onOpen}
      title={c.state_reason ?? undefined}
      className="grid h-8 w-full grid-cols-[22px_1fr_auto_8px] items-center gap-2 rounded-[7px] px-2 text-left text-ui hover:bg-surface-2"
    >
      <span className={cn('grid h-[22px] w-[22px] place-items-center', st === 'unknown' ? 'text-fg-3' : 'text-fg-2')}>
        <DeviceGlyph glyph={glyphFor(c.name, { provenance: c.provenance })} size={16} className={st === 'unknown' ? 'opacity-70' : undefined} />
      </span>
      <span className={cn('truncate', st === 'unknown' ? 'text-fg-3' : 'text-fg')}>{c.name}</span>
      <span className={cn('whitespace-nowrap text-meta', v.cls)}>{v.text}</span>
      <StatusDot status={st} label="" />
      <span className="sr-only">, {hostStateLabel(c.state)}{c.state_reason ? `: ${c.state_reason}` : ''}</span>
    </button>
  );
}

function SummaryRow({ text, state }: { text: string; state?: HealthState }) {
  return (
    <div className={cn('grid h-8 grid-cols-[22px_1fr_8px] items-center gap-2 px-2 text-meta', state === 'unknown' ? 'text-fg-3' : 'text-fg-2')}>
      <span />
      <span className="truncate">{text}</span>
      {state ? <StatusDot status={state} label="" /> : <span />}
    </div>
  );
}

/** Child list hanging off a head node: a trunk with one tick per row. */
function TreeList({ tone, items }: { tone: Tone; items: { key: string | number; node: ReactNode }[] }) {
  return (
    <ul className={cn('relative ml-[17px] mt-2.5 border-l', TRUNK_TONE[tone])}>
      {items.map((it, i) => (
        <li key={it.key} className="relative pl-3.5">
          <span aria-hidden="true" className={cn('absolute left-0 top-1/2 w-2.5 border-t', TICK_TONE[tone])} />
          {i === items.length - 1 && (
            <span aria-hidden="true" className="absolute -left-px bottom-0 top-[calc(50%+1px)] w-[2px] bg-surface" />
          )}
          {it.node}
        </li>
      ))}
    </ul>
  );
}

interface Wire {
  d: string;
  tone: Tone | 'link';
  dashed?: string;
  label?: { x: number; y: number; text: string };
}

const WIRE_STROKE: Record<Wire['tone'], string> = {
  ok: 'var(--ng-line)',
  link: 'var(--ng-line)',
  bad: 'var(--ng-st-down)',
  warn: 'var(--ng-st-warning)',
  unknown: 'var(--ng-st-unknown)',
};

/** Q3 "Which systems are affected?" — roots → parents → affected children. */
export function TopologyCard({ topology, groups, internet, incidents, loading, error, className }: {
  topology: TopologySection | null | undefined;
  groups: HostGroup[] | null | undefined;
  internet: InternetSection | null | undefined;
  incidents: DashboardIncident[];
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const { open, now } = useDashboardCtx();
  const wrapRef = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<string, HTMLElement>());
  const [wires, setWires] = useState<Wire[]>([]);

  const root = topology?.roots[0] ?? null;
  const columns = topology ? topologyColumns(topology, groups ?? null, root ? [root.id] : []) : [];
  const showInternet = !!internet && !!root;
  const colKey = columns.map((c) => `${c.key}:${c.tone}:${c.parent?.parent_id ?? ''}`).join('|');

  const setNode = useCallback((key: string) => (el: HTMLElement | null) => {
    if (el) nodes.current.set(key, el);
    else nodes.current.delete(key);
  }, []);

  // Wires are drawn from the laid-out node boxes, like the prototype.
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const draw = () => {
      const tb = wrap.getBoundingClientRect();
      const box = (k: string) => {
        const el = nodes.current.get(k);
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return { l: b.left - tb.left, t: b.top - tb.top, r: b.right - tb.left, b: b.bottom - tb.top, cx: (b.left + b.right) / 2 - tb.left };
      };
      const out: Wire[] = [];
      const rootBox = box('root');
      const wan = box('internet');
      if (wan && rootBox) {
        const y = (wan.t + wan.b) / 2;
        out.push({ d: `M${wan.r} ${y} H${rootBox.l}`, tone: 'link' });
      }
      if (rootBox) {
        const sx = rootBox.cx;
        const sy = rootBox.b;
        let firstBottom: number | null = null;
        columns.forEach((c, i) => {
          const n = box(c.key);
          if (!n) return;
          if (i === 0) firstBottom = n.b;
          // Stacked (phone): only the first column hangs off the root.
          if (i > 0 && firstBottom !== null && n.t > firstBottom) return;
          const R = 8;
          const my = sy + (n.t - sy) / 2;
          const tx = n.cx;
          let d: string;
          if (Math.abs(tx - sx) < 1) d = `M${sx} ${sy} V${n.t}`;
          else {
            const dir = tx > sx ? 1 : -1;
            d = `M${sx} ${sy} V${my - R} Q${sx} ${my} ${sx + dir * R} ${my} H${tx - dir * R} Q${tx} ${my} ${tx} ${my + R} V${n.t}`;
          }
          const direct = c.kind === 'parent' && c.parent?.parent_id === root?.id;
          out.push({
            d,
            tone: c.tone,
            // via probe = dashed; further down the tree (not a direct child) = dotted
            dashed: c.kind === 'probe' ? '4 4' : direct ? undefined : '2 4',
            label: c.kind === 'probe' ? { x: (tx + sx) / 2 + (tx > sx ? 30 : -30), y: my - 6, text: 'via probe' } : undefined,
          });
        });
      }
      setWires(out);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrap);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- colKey captures the column layout
  }, [colKey, showInternet, root?.id]);

  const wanSt = wanState(internet?.wan?.status);
  const internetSub = internet?.wan
    ? `WAN ${internet.wan.status}${internet.wan.latency_ms != null ? ` · ${fmtMs(internet.wan.latency_ms)} ms` : ''}`
    : internet?.latest
      ? `WAN status unknown · ${fmtMs(internet.latest.latency_ms)} ms`
      : 'WAN status unknown';

  const renderColumn = (c: TopoColumn) => {
    if (c.kind === 'probe' && c.group) {
      const g = c.group;
      return (
        <div key={c.key} className="flex min-w-0 flex-col">
          <TopoNode
            nodeRef={setNode(c.key)}
            glyph="probe"
            name={g.name}
            sub={g.last_report ? `Last heartbeat ${formatWhen(g.last_report, now)}` : 'Never reported'}
            state="unknown"
            tone="unknown"
            srState="probe silent, no data"
            onClick={() => open({ kind: 'group', group: g })}
          />
          <p className="mt-2 px-0.5 text-meta text-fg-2">
            {g.host_count} host{g.host_count === 1 ? '' : 's'} · no data{g.last_report ? ` for ${formatDuration(ageSeconds(g.last_report, now))}` : ''}
          </p>
          <TreeList
            tone="unknown"
            items={[{ key: 'sum', node: <SummaryRow text={`${g.host_count} host${g.host_count === 1 ? '' : 's'} · No data`} state="unknown" /> }]}
          />
        </div>
      );
    }
    const p = c.parent as TopoParent;
    const st = hostState(p.state);
    const related = incidentsForHosts(incidents, [p.id, ...(p.children ?? []).map((k) => k.id)]);
    const inc = related[0];
    const acked = related.length > 0 && related.every((r) => r.acknowledged);
    const glow = c.tone === 'bad' ? 'crit' : c.tone === 'warn' ? 'warn' : null;
    const kids = p.children ?? [];
    const shown = kids.slice(0, MAX_CHILDREN);
    const more = p.child_count - shown.length;
    const sub = `${p.is_gateway ? 'Gateway · ' : ''}${hostStateLabel(p.state)} · ${p.descendant_count} downstream`;
    return (
      <div key={c.key} className="flex min-w-0 flex-col">
        <TopoNode
          nodeRef={setNode(c.key)}
          glyph={glyphFor(p.name, { gateway: p.is_gateway })}
          name={p.name}
          sub={sub}
          state={st}
          tone={c.tone}
          glow={glow}
          acknowledged={acked}
          srState={`${hostStateLabel(p.state)}, worst downstream ${hostStateLabel(p.worst_state)}`}
          onClick={() => open({ kind: 'host', id: p.id, hint: p })}
        />
        {inc && (c.tone === 'bad' || c.tone === 'warn') ? (
          <button
            type="button"
            onClick={() => open({ kind: 'incident', id: inc.id })}
            className={cn('mt-2 flex items-center gap-1.5 px-0.5 text-left text-meta font-medium hover:underline', c.tone === 'bad' ? 'text-down' : 'text-warning')}
          >
            <TriangleAlert size={14} aria-hidden="true" />#{inc.id} · {incidentHostsLabel(inc.host_count)}
          </button>
        ) : p.affected ? (
          <p className="mt-2 px-0.5 text-meta text-fg-2">{describeStates(p.descendant_states)}</p>
        ) : null}
        <TreeList
          tone={c.tone}
          items={
            shown.length > 0
              ? [
                  ...shown.map((k) => ({ key: k.id, node: <ChildRow c={k} onOpen={() => open({ kind: 'host', id: k.id, hint: k })} /> })),
                  ...(more > 0 ? [{ key: 'more', node: <SummaryRow text={`+ ${more} more`} /> }] : []),
                ]
              : [{
                  key: 'sum',
                  node: (
                    <SummaryRow
                      text={p.worst_state === 'up' ? `${p.descendant_count} hosts · all up` : `${p.descendant_count} hosts · ${describeStates(p.descendant_states)}`}
                      state={hostState(p.worst_state)}
                    />
                  ),
                }]
          }
        />
      </div>
    );
  };

  const cols = columns.length;
  return (
    <DashboardCard
      id="dash-topology"
      title="Topology"
      loading={loading}
      error={error}
      className={className}
      actions={
        <>
          {topology && <span className="hidden sm:inline">{topology.parents_total} parent{topology.parents_total === 1 ? '' : 's'}</span>}
          <Link href="/topology" className="text-ui font-medium text-accent hover:text-accent-hover">Open map</Link>
        </>
      }
    >
      {!topology ? (
        <EmptyState compact title="Topology not available" description="The topology could not be built from the current data." />
      ) : !root || cols === 0 ? (
        <EmptyState
          compact
          title="No parent links yet"
          description="Set a parent on hosts, or connect UniFi or Proxmox, to see which hosts sit behind which device."
          action={<Link href="/topology" className="text-ui font-medium text-accent hover:text-accent-hover">Open topology</Link>}
        />
      ) : (
        <>
          <div ref={wrapRef} className="relative flex-1 px-1 pt-1">
            <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
              {wires.map((w, i) => (
                <g key={i}>
                  <path
                    d={w.d}
                    fill="none"
                    strokeLinecap="round"
                    strokeWidth={w.tone === 'ok' || w.tone === 'link' ? 1.5 : 2}
                    strokeDasharray={w.dashed}
                    style={{ stroke: WIRE_STROKE[w.tone], filter: w.tone === 'bad' ? 'var(--ng-wire-glow)' : undefined }}
                  />
                  {w.label && (
                    <text x={w.label.x} y={w.label.y} textAnchor="middle" className="fill-fg-3 text-micro">{w.label.text}</text>
                  )}
                </g>
              ))}
            </svg>
            <div className="relative mb-[46px] flex items-center justify-center gap-14 max-[759px]:mb-[34px] max-[759px]:gap-6">
              {showInternet && (
                <TopoNode
                  nodeRef={setNode('internet')}
                  glyph="globe"
                  name="Internet"
                  sub={internetSub}
                  state={wanSt ?? 'unknown'}
                  tone={wanSt === 'down' ? 'bad' : wanSt === 'ok' ? 'ok' : 'unknown'}
                  glow={wanSt === 'down' ? 'crit' : null}
                  srState={wanSt ? `WAN ${internet?.wan?.status}` : 'WAN status unknown'}
                />
              )}
              <TopoNode
                nodeRef={setNode('root')}
                glyph={glyphFor(root.name, { gateway: root.is_gateway })}
                name={root.name}
                sub={`${root.is_gateway ? 'Gateway' : 'Root'} · ${root.descendant_count} downstream`}
                state={hostState(root.state)}
                tone={root.state === 'down' ? 'bad' : root.state === 'unknown' ? 'unknown' : 'ok'}
                glow={root.state === 'down' ? 'crit' : root.state === 'warning' ? 'warn' : null}
                srState={hostStateLabel(root.state)}
                onClick={() => open({ kind: 'host', id: root.id, hint: root })}
              />
            </div>
            <div className={cn('relative grid gap-4 max-[759px]:grid-cols-1 max-[759px]:gap-[22px]', cols === 1 ? 'grid-cols-1' : cols === 2 ? 'grid-cols-2' : 'grid-cols-3')}>
              {columns.map(renderColumn)}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-border pt-3 text-meta text-fg-3">
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="w-[18px] border-t-2 border-line" />Uplink</span>
            <span className="inline-flex items-center gap-1.5">
              <i aria-hidden="true" className="w-[18px] border-t-2 border-down [filter:var(--ng-wire-glow)]" />Affected · glows
            </span>
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="w-[18px] border-t-2 border-dashed border-unknown" />Via probe · no light = no data</span>
            <span className="ml-auto max-[759px]:ml-0">
              {describeProvenance(topology.links_by_provenance)}
              {topology.unlinked_hosts > 0 && ` · ${topology.unlinked_hosts} without parent`}
            </span>
          </div>
        </>
      )}
    </DashboardCard>
  );
}
