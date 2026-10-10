'use client';

import Link from 'next/link';
import { HealthLegend, HealthRing } from '@/components/ui/HealthRing';
import { StatusPill } from '@/components/ui/StatusPill';
import { EmptyState } from '@/components/ui/EmptyState';
import { healthSegments } from '@/lib/dashboard';
import type { HealthSection, IncidentCounts, SectionError } from '@/types/dashboard';
import { DashboardCard } from './DashboardCard';

interface Props {
  health: HealthSection | null | undefined;
  incidents: IncidentCounts | null | undefined;
  loading: boolean;
  error: SectionError | null;
  /** _integrations/_agents failed: their totals are unknown, not zero. */
  countsError: SectionError | null;
  className?: string;
}

/** Q1 "Is my infrastructure healthy?" — ring of unified host states. */
export function HealthCard({ health, incidents, loading, error, countsError, className }: Props) {
  const segments = health ? healthSegments(health) : [];
  const unknown = health?.counts.unknown ?? 0;
  const critical = incidents?.critical ?? 0;
  const warning = incidents?.warning ?? 0;
  const t = health?.totals;
  const monitored = t ? t.hosts : 0;

  return (
    <DashboardCard
      title="Health"
      loading={loading}
      error={error}
      className={className}
      actions={
        health && (
          <span className="flex flex-wrap justify-end gap-2">
            {critical > 0 && <StatusPill status="down">{critical} critical</StatusPill>}
            {critical === 0 && warning > 0 && <StatusPill status="warning">{warning} warning</StatusPill>}
            {unknown > 0 && <StatusPill status="unknown">{unknown} no data</StatusPill>}
          </span>
        )
      }
    >
      {health && monitored === 0 ? (
        <EmptyState
          compact
          title="No hosts monitored yet"
          description={t && t.disabled > 0 ? `${t.disabled} host${t.disabled === 1 ? ' is' : 's are'} disabled.` : 'Add a host to see its state here.'}
          action={<Link href="/hosts" className="text-ui font-medium text-accent hover:text-accent-hover">Add a host</Link>}
        />
      ) : health ? (
        <>
          <div className="flex flex-1 items-center gap-7 max-[1439px]:gap-[22px] max-[759px]:flex-col max-[759px]:items-stretch">
            <HealthRing
              segments={segments}
              size={184}
              className="max-[1439px]:!h-[168px] max-[1439px]:!w-[168px] max-[759px]:self-center"
              center={
                <>
                  <span className="num font-display text-num-lg font-medium leading-[1.05] tracking-[-0.045em] text-fg">{monitored}</span>
                  <span className="text-meta text-fg-2">Hosts</span>
                </>
              }
            />
            <HealthLegend segments={segments} className="min-w-0 flex-1" />
          </div>
          <div className="mt-[18px] flex flex-wrap gap-x-6 gap-y-1.5 border-t border-border pt-3.5 text-meta text-fg-2">
            <span>
              <b className="num mr-1 text-ui font-medium text-fg">{t!.with_current_data} / {t!.hosts}</b>with current data
            </span>
            {countsError ? (
              <span className="text-fg-3">Agent and integration counts unavailable</span>
            ) : (
              <>
                <span>
                  <b className="num mr-1 text-ui font-medium text-fg">{t!.agents_reporting} / {t!.agents}</b>agents reporting
                </span>
                <span>
                  <b className="num mr-1 text-ui font-medium text-fg">{t!.integrations}</b>integrations
                  {t!.integrations_error > 0 && <span className="ml-1 text-down">· {t!.integrations_error} failing</span>}
                </span>
              </>
            )}
            {t!.probes_stale > 0 && (
              <span className="text-unknown">
                {t!.probes_stale} of {t!.probes} probe{t!.probes === 1 ? '' : 's'} silent
              </span>
            )}
            {t!.disabled > 0 && <span className="text-fg-3">{t!.disabled} disabled, not counted</span>}
          </div>
        </>
      ) : null}
    </DashboardCard>
  );
}
