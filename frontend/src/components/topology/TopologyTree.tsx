'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Box, Cloud, Cpu, Globe, HardDrive, Monitor, Network, Radio, Router, Server, Wifi, type LucideIcon,
} from 'lucide-react';
import { StatusDot } from '@/components/ui/StatusDot';
import { HEALTH_LABEL, STATE_TEXT, type HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { countDescendants, nodeState, subtreeHas, type TopoNode, type TreeNode } from './model';

/** Device glyph from the data source / check type (the API has no device class). */
function glyph(n: TopoNode): LucideIcon {
  const s = `${n.source} ${n.check_type}`.toLowerCase();
  if (s.includes('unifi')) return /ap|wifi|wlan/.test(n.name.toLowerCase()) ? Wifi : Router;
  if (s.includes('proxmox')) return /vm|lxc|ct/.test(s) ? Box : Server;
  if (s.includes('agent')) return Monitor;
  if (s.includes('synology') || s.includes('truenas') || s.includes('unas') || s.includes('nas')) return HardDrive;
  if (s.includes('snmp')) return Network;
  if (s.includes('http') || s.includes('ssl')) return Globe;
  if (s.includes('probe')) return Radio;
  if (s.includes('cloud')) return Cloud;
  if (s.includes('redfish') || s.includes('ipmi')) return Cpu;
  return Server;
}

/** Right-hand value of a row: the state word for anything that is not OK. */
function rowValue(st: HealthState, n: TopoNode): { text: string; cls: string } {
  if (st === 'ok') return { text: n.check_type, cls: 'text-fg-3' };
  return { text: HEALTH_LABEL[st], cls: cn(STATE_TEXT[st], st === 'down' && 'font-medium') };
}

const LIST_LIMIT = 12;

function Row({ t }: { t: TreeNode }) {
  const n = t.node;
  const st = nodeState(n);
  const Icon = glyph(n);
  const v = rowValue(st, n);
  return (
    <Link
      prefetch={false}
      href={`/hosts/${n.id}`}
      className="grid h-[32px] w-full min-w-0 grid-cols-[22px_minmax(0,1fr)_auto_10px] items-center gap-2 rounded-ng-sm px-2 text-ui hover:bg-surface-2"
    >
      <span className={cn('grid place-items-center', st === 'unknown' ? 'text-fg-3 opacity-70' : 'text-fg-2')} aria-hidden="true">
        <Icon size={15} />
      </span>
      <span className={cn('truncate', st === 'unknown' ? 'text-fg-3' : 'text-fg')} title={`${n.name} · ${n.hostname}`}>{n.name}</span>
      <span className={cn('whitespace-nowrap text-meta', v.cls)}>{v.text}</span>
      {/* Non-OK rows already say their state in words. */}
      <StatusDot status={st} label={st === 'ok' ? 'OK' : ''} />
    </Link>
  );
}

/** Nested branch list with E3 tree lines (solid uplink, red affected branch, dashed no-data). */
function Branch({ items, tone }: { items: TreeNode[]; tone: 'bad' | 'unk' | null }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, LIST_LIMIT);
  const hidden = items.length - visible.length;
  const hiddenDown = items.slice(LIST_LIMIT).filter((c) => nodeState(c.node) === 'down' || subtreeHas(c, 'down')).length;
  return (
    <ul
      className={cn(
        'relative ml-[17px] mt-2 border-l',
        tone === 'bad' && 'border-down/60 [box-shadow:var(--ng-branch-glow)]',
        tone === 'unk' && 'border-dashed border-unknown',
        tone === null && 'border-line',
      )}
    >
      {visible.map((c) => {
        const childTone = nodeState(c.node) === 'down' || subtreeHas(c, 'down') ? 'bad' : nodeState(c.node) === 'unknown' ? 'unk' : tone === 'unk' ? 'unk' : null;
        return (
          <li
            key={c.node.id}
            className={cn(
              'relative pl-[14px]',
              // horizontal stub
              'before:absolute before:left-0 before:top-[16px] before:w-[10px] before:border-t',
              tone === 'bad' ? 'before:border-down/60' : tone === 'unk' ? 'before:border-dashed before:border-unknown' : 'before:border-line',
              // hide the vertical line below the last item
              'last:after:absolute last:after:-left-px last:after:bottom-0 last:after:top-[17px] last:after:w-px last:after:bg-surface',
            )}
          >
            <Row t={c} />
            {c.children.length > 0 && <Branch items={c.children} tone={childTone} />}
          </li>
        );
      })}
      {hidden > 0 && (
        <li className="relative pl-[14px] before:absolute before:left-0 before:top-[16px] before:w-[10px] before:border-t before:border-line last:after:absolute last:after:-left-px last:after:bottom-0 last:after:top-[17px] last:after:w-px last:after:bg-surface">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="flex h-[32px] w-full items-center gap-2 rounded-ng-sm px-2 text-left text-meta text-fg-2 hover:bg-surface-2 hover:text-fg"
          >
            + {hidden} more{hiddenDown > 0 && <span className="text-down">· {hiddenDown} affected</span>}
          </button>
        </li>
      )}
    </ul>
  );
}

function HeadNode({ t, bad }: { t: TreeNode; bad: boolean }) {
  const n = t.node;
  const st = nodeState(n);
  const Icon = glyph(n);
  const below = countDescendants(t);
  const downBelow = bad && st !== 'down';
  return (
    <Link
      prefetch={false}
      href={`/hosts/${n.id}`}
      className={cn(
        'relative flex min-w-0 items-center gap-2.5 rounded-[9px] border bg-surface-2 py-2 pl-2 pr-3 transition-colors hover:border-border-2 hover:bg-surface-3',
        st === 'down' ? 'ng-glow-crit border-down/55 bg-down-soft' : downBelow ? 'border-down/40' : st === 'unknown' ? 'border-dashed border-border-2' : 'border-border',
      )}
    >
      <span className={cn('relative grid h-9 w-9 shrink-0 place-items-center rounded-[8px] bg-surface', st === 'unknown' ? 'text-fg-3' : 'text-fg')} aria-hidden="true">
        <Icon size={19} className={st === 'unknown' ? 'opacity-70' : undefined} />
        <span className="absolute -bottom-[3px] -right-[3px] rounded-full border-2 border-surface-2 bg-surface-2">
          <StatusDot status={st} size="lg" label="" />
        </span>
      </span>
      <span className="min-w-0">
        <span className="block truncate text-ui font-medium text-fg">{n.name}</span>
        <span className="block truncate text-meta text-fg-2">
          {st === 'ok' ? `${below} downstream` : `${HEALTH_LABEL[st]} · ${below} downstream`}
        </span>
      </span>
      <span className="sr-only">, {HEALTH_LABEL[st]}</span>
    </Link>
  );
}

function Column({ t }: { t: TreeNode }) {
  const st = nodeState(t.node);
  const bad = st === 'down' || subtreeHas(t, 'down');
  const unk = !bad && st === 'unknown';
  const downCount = t.children.length ? countByState(t, 'down') : 0;
  const unknownCount = countByState(t, 'unknown');
  const tone = bad ? 'bad' : unk ? 'unk' : null;
  return (
    <li className="flex min-w-0 flex-col">
      <HeadNode t={t} bad={bad} />
      {bad && downCount > 0 && (
        <p className="mt-2 px-0.5 text-meta font-medium text-down">{downCount} down below {t.node.name}</p>
      )}
      {unk && unknownCount > 0 && (
        <p className="mt-2 px-0.5 text-meta text-fg-2">{unknownCount} hosts · no data</p>
      )}
      <Branch items={t.children} tone={tone} />
    </li>
  );
}

function countByState(t: TreeNode, state: HealthState): number {
  return t.children.reduce((sum, c) => sum + (nodeState(c.node) === state ? 1 : 0) + countByState(c, state), 0);
}

/**
 * Tree view of the topology (E3 dashboard glyphs): one column per root with
 * its downstream hosts. Every node is a link, so the whole map works with the
 * keyboard and a screen reader. Only the critical branch glows.
 */
export function TopologyTree({ trees, orphans }: { trees: TreeNode[]; orphans: TopoNode[] }) {
  return (
    <div className="space-y-6">
      {trees.length > 0 && (
        <ul aria-label="Dependency trees" className="grid gap-x-4 gap-y-6 [grid-template-columns:repeat(auto-fill,minmax(min(100%,280px),1fr))]">
          {trees.map((t) => <Column key={t.node.id} t={t} />)}
        </ul>
      )}
      {orphans.length > 0 && (
        <div>
          <h3 className="mb-2 text-meta font-medium text-fg-2">Without parent · {orphans.length}</h3>
          <ul className="grid gap-x-4 [grid-template-columns:repeat(auto-fill,minmax(min(100%,240px),1fr))]">
            {orphans.map((n) => (
              <li key={n.id} className="min-w-0"><Row t={{ node: n, children: [] }} /></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
