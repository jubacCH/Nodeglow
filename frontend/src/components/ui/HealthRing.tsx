'use client';

import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { HEALTH_LABEL, STATE_FILL, STATE_VAR, describeSegments, type HealthSegment, type HealthState } from '@/lib/status';

export { describeSegments, type HealthSegment };

interface HealthRingProps {
  segments: HealthSegment[];
  /** Diameter in px (E3: 184, 168 below 1440px). */
  size?: number;
  /** Ring thickness in viewBox units (viewBox 200). */
  thickness?: number;
  /** Content in the middle, e.g. <BigNumber value={48} label="Hosts" />. */
  center?: ReactNode;
  /** Noun for the aria-label ("48 hosts: …"). */
  noun?: string;
  className?: string;
}

const ORDER: HealthState[] = ['ok', 'degraded', 'warning', 'down', 'maint', 'unknown'];

/**
 * Donut of object states (E3 health ring). Segments keep a fixed state order,
 * "no data" is hatched (never a colour that could read as healthy), and the
 * centre gets a faint inner glow in the overall severity: red-orange when
 * anything is down, orange for warnings, none otherwise. Segments stay sharp.
 */
export function HealthRing({ segments, size = 184, thickness = 14, center, noun = 'hosts', className }: HealthRingProps) {
  const uid = useId().replace(/:/g, '');
  const hatchId = `ng-hatch-${uid}`;
  const ordered = [...segments].sort((a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state));
  const total = ordered.reduce((a, s) => a + s.count, 0);
  const r = 80;
  const C = 2 * Math.PI * r;
  const gap = ordered.filter((s) => s.count > 0).length > 1 ? 3 : 0;
  const worst = ordered.some((s) => s.state === 'down' && s.count > 0)
    ? 'crit'
    : ordered.some((s) => s.state === 'warning' && s.count > 0)
      ? 'warn'
      : null;

  let offset = 0;
  const arcs = ordered.filter((s) => s.count > 0).map((s) => {
    const len = (s.count / total) * C;
    const dash = Math.max(len - gap, 1);
    const arc = { s, dash, offset };
    offset += len;
    return arc;
  });

  return (
    <div className={cn('relative shrink-0', className)} style={{ width: size, height: size }}>
      {worst && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-[14%] animate-breathe rounded-full"
          style={{
            background: `radial-gradient(circle closest-side, var(${worst === 'crit' ? '--ng-ring-core' : '--ng-ring-core-warn'}) 0%, color-mix(in srgb, var(${worst === 'crit' ? '--ng-ring-core' : '--ng-ring-core-warn'}) 45%, transparent) 55%, transparent 100%)`,
          }}
        />
      )}
      <svg viewBox="0 0 200 200" className="relative block h-full w-full" role="img" aria-label={describeSegments(ordered, noun)}>
        <defs>
          <pattern id={hatchId} patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)">
            <rect width="5" height="5" style={{ fill: 'var(--ng-hatch-2)' }} />
            <rect width="2" height="5" style={{ fill: 'var(--ng-st-unknown)' }} />
          </pattern>
        </defs>
        <circle cx="100" cy="100" r={r} fill="none" strokeWidth={thickness} style={{ stroke: 'var(--ng-surface-2)' }} />
        {arcs.map(({ s, dash, offset: off }) => (
          <circle
            key={s.state}
            cx="100"
            cy="100"
            r={r}
            fill="none"
            strokeWidth={thickness}
            strokeDasharray={`${dash} ${C - dash}`}
            strokeDashoffset={-off}
            transform="rotate(-90 100 100)"
            style={{ stroke: s.state === 'unknown' ? `url(#${hatchId})` : STATE_VAR[s.state] }}
          >
            <title>{`${s.label ?? HEALTH_LABEL[s.state]} · ${s.count}${s.detail ? ` · ${s.detail}` : ''}`}</title>
          </circle>
        ))}
      </svg>
      {center && <div className="pointer-events-none absolute inset-0 grid place-content-center text-center">{center}</div>}
    </div>
  );
}

/** Legend rows for a HealthRing (swatch, label + detail, count). */
export function HealthLegend({ segments, className }: { segments: HealthSegment[]; className?: string }) {
  const ordered = [...segments].sort((a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state));
  return (
    <ul className={cn('grid min-w-0 gap-0.5', className)}>
      {ordered.map((s) => (
        <li key={s.state} className="grid grid-cols-[10px_1fr_auto] items-center gap-2.5 border-b border-border py-1.5 last:border-b-0">
          <span aria-hidden="true" className={cn('h-2.5 w-2.5 rounded-[3px]', STATE_FILL[s.state])} />
          <span className="min-w-0 text-ui text-fg">
            {s.label ?? HEALTH_LABEL[s.state]}
            {s.detail && <small className="block truncate text-meta leading-tight text-fg-3">{s.detail}</small>}
          </span>
          <span className="num text-[15px] font-medium text-fg">{s.count}</span>
        </li>
      ))}
    </ul>
  );
}
