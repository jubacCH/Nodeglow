'use client';

/**
 * Shared building blocks for the integration detail views
 * (`components/integrations/*Detail.tsx`). Everything here follows the
 * design-system rules: missing numbers render "—" (never 0), unknown state is
 * never green, bars are coloured by threshold and hatched without data.
 */
import type { ReactNode } from 'react';
import type { EChartsOption } from 'echarts';
import { Card, CardHeader } from '@/components/ui/Card';
import { BigNumber } from '@/components/ui/BigNumber';
import { StatusDot } from '@/components/ui/StatusDot';
import { Pill } from '@/components/ui/Tag';
import { TableContainer } from '@/components/ui/Table';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import { STATE_FILL, type HealthState } from '@/lib/status';
import { cn, formatUptime } from '@/lib/utils';

/* ------------------------------------------------------------------ */
/* Value helpers                                                       */
/* ------------------------------------------------------------------ */

/** True for a finite number (null, undefined, NaN and strings are "no data"). */
export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Fixed-point number or null when there is no value. */
export function fixed(v: unknown, digits = 1): string | null {
  return isNum(v) ? v.toFixed(digits) : null;
}

/** Number with optional unit ("12.3 %"), or null. */
export function withUnit(v: unknown, unit: string, digits = 1): string | null {
  const f = fixed(v, digits);
  return f === null ? null : `${f} ${unit}`;
}

/** Compact count (1.2K, 3.4M), or null. */
export function formatCount(n: unknown): string | null {
  if (!isNum(n)) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Binary byte size (1024-based), or null. */
export function formatBytes(bytes: unknown): string | null {
  if (!isNum(bytes)) return null;
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

/** Uptime/runtime from seconds, or null. */
export function uptime(seconds: unknown): string | null {
  return isNum(seconds) && seconds >= 0 ? formatUptime(seconds) : null;
}

/** Locale date/time, or null for missing or unparsable input. */
export function formatDate(v: string | number | null | undefined, mode: 'datetime' | 'date' = 'datetime'): string | null {
  if (v === null || v === undefined || v === '') return null;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return null;
  return mode === 'date' ? d.toLocaleDateString() : d.toLocaleString();
}

/** Usage threshold → state (≥ crit down, ≥ warn warning, else ok). */
export function usageState(pct: unknown, warn = 75, crit = 90): HealthState {
  if (!isNum(pct)) return 'unknown';
  if (pct >= crit) return 'down';
  if (pct >= warn) return 'warning';
  return 'ok';
}

/** Temperature → text class (only hot values are coloured). */
export function tempClass(temp: unknown, warn: number, crit: number): string {
  if (!isNum(temp)) return 'text-fg-3';
  if (temp >= crit) return 'text-down';
  if (temp >= warn) return 'text-warning';
  return 'text-fg';
}

/** Keys that look like credentials are never shown in clear text. */
export function isSecretKey(key: string): boolean {
  return /pass(word)?|secret|token|api[_-]?key|private[_-]?key|psk|credential/i.test(key);
}

/* ------------------------------------------------------------------ */
/* Small presentational parts                                          */
/* ------------------------------------------------------------------ */

/** Renders its value, or a grey em dash when there is none. */
export function Val({ children, className }: { children: ReactNode; className?: string }) {
  const empty = children === null || children === undefined || children === '' || children === false;
  if (empty) return <span className="text-fg-3">—</span>;
  return <span className={className}>{children}</span>;
}

/** Heading for a group of cards outside a single card. */
export function SectionTitle({ children, meta }: { children: ReactNode; meta?: ReactNode }) {
  return (
    <div className="mb-3 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
      <h2 className="text-body font-medium text-fg">{children}</h2>
      {meta && <span className="text-meta text-fg-3">{meta}</span>}
    </div>
  );
}

/** Key figure tile. `value` null/undefined → "—". */
export function StatTile({
  label,
  value,
  unit,
  state,
}: {
  label: ReactNode;
  value: ReactNode | null | undefined;
  unit?: ReactNode;
  state?: HealthState;
}) {
  return (
    <Card padding="sm">
      <BigNumber size="sm" value={value} unit={unit} label={label} state={state} className="[&>div:first-child]:truncate" />
    </Card>
  );
}

/** Responsive grid for StatTiles. */
export function StatGrid({ children, cols = 4 }: { children: ReactNode; cols?: 3 | 4 | 5 }) {
  return (
    <div
      className={cn(
        'grid grid-cols-2 gap-4',
        cols === 3 && 'md:grid-cols-3',
        cols === 4 && 'md:grid-cols-4',
        cols === 5 && 'md:grid-cols-3 lg:grid-cols-5',
      )}
    >
      {children}
    </div>
  );
}

/** Label/value grid (definition list). */
export function KVGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn('grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4', className)}>{children}</dl>;
}

/** One stacked label/value pair inside KVGrid. */
export function KV({ label, children, mono }: { label: ReactNode; children: ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta text-fg-3">{label}</dt>
      <dd className={cn('mt-1 text-ui text-fg', mono ? 'break-all font-mono' : 'break-words')}>
        <Val>{children}</Val>
      </dd>
    </div>
  );
}

/** Horizontal label/value rows (dl). */
export function KVList({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn('space-y-2', className)}>{children}</dl>;
}

export function KVRow({ label, children, mono }: { label: ReactNode; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 text-ui">
      <dt className="shrink-0 text-fg-3">{label}</dt>
      <dd className={cn('min-w-0 text-right text-fg', mono ? 'break-all font-mono' : 'break-words')}>
        <Val>{children}</Val>
      </dd>
    </div>
  );
}

/**
 * Usage/threshold bar. Fill by state (default: ≥90 down, ≥75 warning, else
 * ok); without a value the track is hatched and the text reads "—".
 */
export function UsageBar({
  label,
  pct,
  detail,
  state,
}: {
  label: string;
  pct: number | null | undefined;
  /** Text right of the label; defaults to the percentage. */
  detail?: ReactNode;
  /** Override the threshold colouring (e.g. battery: low is bad). */
  state?: HealthState;
}) {
  const has = isNum(pct);
  const s = has ? (state ?? usageState(pct)) : 'unknown';
  const width = has ? Math.max(0, Math.min(pct, 100)) : 0;
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-meta">
        <span className="shrink-0 text-fg-2">{label}</span>
        <span className={cn('num truncate', has ? 'text-fg' : 'text-fg-3')}>
          {has ? (detail ?? `${pct.toFixed(1)} %`) : '—'}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={has ? Math.round(width) : undefined}
        aria-valuetext={has ? `${pct.toFixed(1)} %` : 'No data'}
        className={cn('h-[6px] overflow-hidden rounded-pill', has ? 'bg-surface-3' : 'ng-hatch')}
      >
        {has && <div className={cn('h-full rounded-pill', STATE_FILL[s])} style={{ width: `${width}%` }} />}
      </div>
    </div>
  );
}

/** Dot + text, for status cells. `disabled` = intentionally off (dimmed). */
export function StateLabel({
  status,
  children,
  className,
}: {
  status: HealthState | 'disabled';
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)}>
      <StatusDot status={status} label="" />
      <span className={cn('truncate', status === 'disabled' || status === 'unknown' ? 'text-fg-3' : 'text-fg')}>
        {children}
      </span>
    </span>
  );
}

/** Configuration flag (enabled/disabled) — not a health state, so no green. */
export function EnabledPill({ enabled, on = 'Enabled', off = 'Disabled' }: { enabled: boolean | null | undefined; on?: string; off?: string }) {
  if (enabled === null || enabled === undefined) return <span className="text-fg-3">—</span>;
  return enabled ? <Pill tone="accent">{on}</Pill> : <Pill>{off}</Pill>;
}

/** Card with a header and a table that scrolls inside the card. */
export function TableCard({
  title,
  meta,
  actions,
  children,
  maxHeight,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  maxHeight?: number;
}) {
  return (
    <Card as="section" padding="none">
      <CardHeader title={title} meta={meta} actions={actions} className="mb-2 px-4 pt-4" />
      <TableContainer maxHeight={maxHeight}>{children}</TableContainer>
    </Card>
  );
}

/** Top-N list (domains, clients) with counts. */
export function TopList({ title, rows }: { title: string; rows: { label: string; count: number | null | undefined }[] }) {
  return (
    <Card as="section">
      <CardHeader title={title} />
      {rows.length === 0 ? (
        <p className="text-ui text-fg-3">No data</p>
      ) : (
        <ol className="space-y-2">
          {rows.slice(0, 10).map((row, i) => (
            <li key={`${row.label}-${i}`} className="flex min-w-0 items-center justify-between gap-2 text-ui">
              <span className="truncate font-mono text-fg-2">{row.label || '—'}</span>
              <span className="num shrink-0 text-fg-3">{formatCount(row.count) ?? '—'}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/** Donut "blocked vs allowed" for DNS filters. Categorical colours, not status. */
export function BlockedAllowedChart({ blocked, total }: { blocked: number | null | undefined; total: number | null | undefined }) {
  const t = useChartTheme();
  const has = isNum(blocked) && isNum(total) && total > 0;
  const allowed = has ? Math.max(0, total - blocked) : 0;
  const option: EChartsOption = {
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: t.surface, borderWidth: 2 },
        label: { show: false },
        data: [
          { value: has ? blocked : 0, name: 'Blocked', itemStyle: { color: t.series[2] } },
          { value: allowed, name: 'Allowed', itemStyle: { color: t.series[0] } },
        ],
      },
    ],
  };
  return (
    <Card as="section">
      <CardHeader title="Blocked vs allowed" />
      {has ? (
        <>
          <EChart option={option} height={200} ariaLabel={`${blocked} of ${total} queries blocked, ${allowed} allowed`} />
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-meta text-fg-2">
            <span className="num">Blocked {formatCount(blocked)}</span>
            <span className="num">Allowed {formatCount(allowed)}</span>
          </div>
        </>
      ) : (
        <div className="ng-hatch flex h-[200px] items-center justify-center rounded-ctl">
          <span className="rounded-chip bg-surface px-2 py-1 text-meta text-fg-3">No query data</span>
        </div>
      )}
    </Card>
  );
}
