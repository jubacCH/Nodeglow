'use client';

import { BigNumber } from '@/components/ui/BigNumber';
import { StatusPill } from '@/components/ui/StatusPill';
import { availabilityScale, fmtNum, fmtPct } from '@/lib/dashboard';
import type { AvailabilitySection, SectionError } from '@/types/dashboard';
import { DashboardCard, KeyValues } from './DashboardCard';

/** 30-day availability against the target and its downtime budget. */
export function AvailabilityCard({ availability: a, loading, error, className }: {
  availability: AvailabilitySection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const scale = a ? availabilityScale(a) : null;
  const target = a ? `${a.target_pct.toFixed(2)} %` : '';

  return (
    <DashboardCard
      id="dash-availability"
      title="Availability"
      loading={loading}
      error={error}
      className={className}
      actions={a && <span>{a.window_days} days</span>}
    >
      {a && scale && (
        <>
          <BigNumber value={a.pct !== null ? fmtPct(a.pct) : null} unit="%" label={a.status === 'no_data' ? 'No checks in this window' : undefined} />
          <div className="mt-2">
            {a.status === 'above_target' && <StatusPill status="ok">Above target {target}</StatusPill>}
            {a.status === 'below_target' && <StatusPill status="warning">Below target {target}</StatusPill>}
            {a.status === 'no_data' && <StatusPill status="unknown">No data · target {target}</StatusPill>}
          </div>
          <div
            role="img"
            aria-label={a.pct !== null
              ? `${fmtPct(a.pct)} percent on a scale from ${fmtPct(scale.lo)} to 100 percent, target ${target}`
              : `No availability data, target ${target}`}
            className="relative mb-1.5 mt-[22px] h-2 rounded-[4px] bg-surface-3"
          >
            {scale.fillPct !== null && (
              <i className={`absolute inset-y-0 left-0 rounded-[4px] ${a.status === 'below_target' ? 'bg-warning' : 'bg-ok'}`} style={{ width: `${scale.fillPct}%` }} />
            )}
            {scale.fillPct === null && <i className="ng-hatch absolute inset-0 rounded-[4px]" />}
            <b className="absolute -bottom-1.5 -top-1.5 w-0.5 -translate-x-1/2 rounded-[1px] bg-fg" style={{ left: `${scale.targetPct}%` }} />
          </div>
          <div aria-hidden="true" className="relative flex justify-between text-micro text-fg-3">
            <span className="num">{fmtPct(scale.lo)}</span>
            <span className="absolute -translate-x-1/2 text-fg-2" style={{ left: `${scale.targetPct}%` }}>target</span>
            <span className="num">100 %</span>
          </div>
          <KeyValues
            className="mt-auto"
            rows={[
              ['Downtime', a.downtime_minutes !== null
                ? `${fmtNum(a.downtime_minutes, 0)} of ${fmtNum(a.budget_minutes, 0)} min budget`
                : `— of ${fmtNum(a.budget_minutes, 0)} min budget`],
              ['Hosts with data', `${a.hosts_with_data} / ${a.hosts_total}`],
            ]}
          />
          <p className="mt-2.5 text-meta text-fg-3">
            Maintenance and no-data periods are excluded, never counted as up. Downtime is fleet-equivalent.
          </p>
        </>
      )}
    </DashboardCard>
  );
}
