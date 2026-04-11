'use client';

/**
 * HostHealthGrid — the "fleet at a glance" dashboard widget.
 *
 * Replaces the previous 3D gravity globe. The globe was beautiful but it
 * used 380px of prime real estate to tell you less than the six stat cards
 * already do. This component uses the same space to show EVERY host at
 * once as a dense colored tile, sorted worst-first so broken hosts always
 * land in the top-left of your visual field.
 *
 * Scales from 5 hosts (still looks intentional) to ~500 hosts (still fits
 * in one viewport). Each tile is 20px × 20px with a 4px gap; that's ~300
 * tiles in 380px at 1920 width.
 *
 * Interactions:
 * - Hover  → tooltip with name, state, latency
 * - Click  → deep-link to /hosts/{id}
 * - Offline tiles pulse subtly (red shadow animation)
 * - Sort order: offline > error > maintenance > unknown > online, then by name
 */

import Link from 'next/link';
import { useMemo } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { cn, formatLatency } from '@/lib/utils';
import type { HostStat } from '@/hooks/queries/useDashboard';

type TileState = 'online' | 'offline' | 'error' | 'maintenance' | 'unknown';

function stateOf(h: HostStat): TileState {
  if (h.host.maintenance) return 'maintenance';
  if (h.online === false) return 'offline';
  if (h.online === null) return 'unknown';
  if (h.host.port_error) return 'error';
  return 'online';
}

// Severity order for sort: lower number = worse = earlier in the grid.
const STATE_RANK: Record<TileState, number> = {
  offline: 0,
  error: 1,
  maintenance: 2,
  unknown: 3,
  online: 4,
};

// Tailwind background classes — declared statically so JIT picks them up.
const TILE_BG: Record<TileState, string> = {
  offline: 'bg-red-500/80 hover:bg-red-500 shadow-[0_0_8px_rgba(248,113,113,0.35)]',
  error: 'bg-orange-500/80 hover:bg-orange-500',
  maintenance: 'bg-amber-500/70 hover:bg-amber-500',
  unknown: 'bg-slate-600/60 hover:bg-slate-500',
  online: 'bg-emerald-500/60 hover:bg-emerald-400',
};

export interface HostHealthGridProps {
  hosts: HostStat[];
}

export function HostHealthGrid({ hosts }: HostHealthGridProps) {
  const sorted = useMemo(() => {
    return [...hosts].sort((a, b) => {
      const sa = STATE_RANK[stateOf(a)];
      const sb = STATE_RANK[stateOf(b)];
      if (sa !== sb) return sa - sb;
      return a.host.name.localeCompare(b.host.name);
    });
  }, [hosts]);

  const counts = useMemo(() => {
    const c = { online: 0, offline: 0, error: 0, maintenance: 0, unknown: 0 };
    for (const h of hosts) c[stateOf(h)]++;
    return c;
  }, [hosts]);

  const healthPct = hosts.length
    ? Math.round(((counts.online + counts.maintenance) / hosts.length) * 100)
    : 100;

  return (
    <GlassCard
      className={cn(
        'relative overflow-hidden p-4',
        counts.offline > 0 && 'border-red-500/25',
      )}
    >
      {/* Header bar — counts + overall health % */}
      <div className="flex items-center justify-between mb-3 pb-2 border-b" style={{ borderColor: 'var(--ng-card-border)' }}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-300">
            Fleet Health
          </span>
          <span className="text-[11px] text-slate-500 tabular-nums">{hosts.length} hosts</span>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {counts.offline > 0 && (
            <Pill state="offline" count={counts.offline} />
          )}
          {counts.error > 0 && (
            <Pill state="error" count={counts.error} />
          )}
          {counts.maintenance > 0 && (
            <Pill state="maintenance" count={counts.maintenance} />
          )}
          <Pill state="online" count={counts.online} />
          <span
            className={cn(
              'text-[10px] font-mono tabular-nums px-2 py-0.5 rounded-full border',
              healthPct >= 99
                ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10'
                : healthPct >= 95
                  ? 'text-amber-400 border-amber-500/30 bg-amber-500/10'
                  : 'text-red-400 border-red-500/30 bg-red-500/10',
            )}
            title="Overall health percentage"
          >
            {healthPct}%
          </span>
        </div>
      </div>

      {/* Tile grid — sorted worst-first so trouble is top-left */}
      <div
        className="flex flex-wrap gap-[3px] overflow-y-auto"
        style={{ maxHeight: 320 }}
      >
        {sorted.map((h) => {
          const state = stateOf(h);
          const latencyLabel = formatLatency(h.latency);
          const title = `${h.host.name}\n${state}${latencyLabel !== '—' ? ' · ' + latencyLabel : ''}`;
          return (
            <Link
              key={h.host.id}
              href={`/hosts/${h.host.id}`}
              title={title}
              aria-label={`${h.host.name}: ${state}`}
              className={cn(
                'w-5 h-5 rounded-sm transition-all duration-150',
                'active:scale-90 hover:scale-110',
                TILE_BG[state],
                state === 'offline' && 'animate-pulse',
              )}
            />
          );
        })}
        {sorted.length === 0 && (
          <p className="text-xs text-slate-500 py-6 w-full text-center">
            No hosts yet — add your first host to populate this grid.
          </p>
        )}
      </div>

      {/* Legend — subtle, monospace, bottom right */}
      <div className="mt-3 pt-2 border-t flex items-center justify-end gap-3 text-[10px] text-slate-600 font-mono" style={{ borderColor: 'var(--ng-card-border)' }}>
        <span>click any tile →</span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-emerald-500/60" /> healthy
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-amber-500/70" /> maintenance
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-sm bg-red-500/80" /> offline
        </span>
      </div>
    </GlassCard>
  );
}

function Pill({ state, count }: { state: TileState; count: number }) {
  return (
    <span
      className="flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full font-mono tabular-nums"
      style={{
        background: 'var(--ng-card-bg)',
        border: '1px solid var(--ng-card-border)',
      }}
    >
      <StatusDot
        status={state === 'error' ? 'offline' : state}
        pulse={state === 'offline'}
        size="sm"
      />
      <span
        className={
          state === 'online'
            ? 'text-emerald-400'
            : state === 'offline'
              ? 'text-red-400'
              : state === 'error'
                ? 'text-orange-400'
                : state === 'maintenance'
                  ? 'text-amber-400'
                  : 'text-slate-400'
        }
      >
        {count}
      </span>
    </span>
  );
}
