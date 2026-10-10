'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { BigNumber } from '@/components/ui/BigNumber';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusPill } from '@/components/ui/StatusPill';
import {
  describeSpeedHistory, fmtMs, formatAgo, formatWhen, hostState, isStale, speedBars, wanState,
} from '@/lib/dashboard';
import { cn } from '@/lib/utils';
import type { InternetSection, SectionError } from '@/types/dashboard';
import { DashboardCard, KeyValues, StaleHint, useDashboardCtx } from './DashboardCard';
import { StatusDot } from '@/components/ui/StatusDot';

/** A speed test older than this is flagged (tests usually run hourly). */
const SPEEDTEST_STALE_S = 3 * 3600;

export function InternetCard({ internet, loading, error, className }: {
  internet: InternetSection | null | undefined;
  loading: boolean;
  error: SectionError | null;
  className?: string;
}) {
  const { now, open } = useDashboardCtx();
  const wan = wanState(internet?.wan?.status);
  const latest = internet?.latest ?? null;
  const stale = latest ? isStale(latest.at, now, SPEEDTEST_STALE_S) : false;
  const bars = internet ? speedBars(internet.history) : [];
  const source = internet?.source === 'unifi' ? 'UniFi gateway speed test' : internet?.source === 'speedtest' ? 'Speedtest integration' : 'UniFi';

  return (
    <DashboardCard
      title="Internet"
      loading={loading}
      error={error}
      className={className}
      actions={wan && <StatusPill status={wan}>{wan === 'ok' ? 'WAN up' : wan === 'down' ? 'WAN down' : `WAN ${internet?.wan?.status}`}</StatusPill>}
    >
      {!internet ? (
        <EmptyState
          compact
          title="No internet data"
          description="Connect a Speedtest or UniFi integration to see bandwidth and WAN status."
          action={<Link href="/integration/store" className="text-ui font-medium text-accent hover:text-accent-hover">Browse integrations</Link>}
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1.5 flex items-center gap-1.5 text-meta text-fg-2">
                <ArrowDown size={14} className="text-accent" aria-hidden="true" /> Download
              </div>
              <BigNumber value={latest?.download_mbps != null ? Math.round(latest.download_mbps) : null} unit="Mbit/s" stale={stale} />
            </div>
            <div>
              <div className="mb-1.5 flex items-center gap-1.5 text-meta text-fg-2">
                <ArrowUp size={14} className="text-accent" aria-hidden="true" /> Upload
              </div>
              <BigNumber value={latest?.upload_mbps != null ? Math.round(latest.upload_mbps) : null} unit="Mbit/s" stale={stale} />
            </div>
          </div>

          {bars.length > 0 && (
            <div className="mt-[22px]">
              <div className="flex justify-between text-meta text-fg-2">
                <span>Download · last {bars.length} test{bars.length === 1 ? '' : 's'}</span>
                {stale ? <StaleHint>Last test {formatAgo(latest?.at, now)}</StaleHint> : <span className="text-fg-3">{formatAgo(latest?.at, now)}</span>}
              </div>
              <div
                role="img"
                aria-label={describeSpeedHistory(internet.history)}
                className={cn('mt-2.5 flex h-14 items-end gap-[3px]', stale && 'opacity-60')}
              >
                {bars.map((b, i) => (
                  <span
                    key={`${b.at}-${i}`}
                    title={`${formatWhen(b.at, now)} · ${b.value != null ? `${Math.round(b.value)} Mbit/s down` : 'no result'}`}
                    className={cn('min-h-[2px] flex-1 rounded-[2px]', i === bars.length - 1 ? 'bg-accent' : 'bg-surface-3')}
                    style={{ height: `${b.heightPct}%` }}
                  />
                ))}
              </div>
            </div>
          )}

          <KeyValues
            className="mt-auto"
            rows={[
              ['Latency', `${fmtMs(latest?.latency_ms ?? internet.wan?.latency_ms)} ms`],
              ...(internet.gateway
                ? [[
                    'Gateway',
                    <button
                      key="gw"
                      type="button"
                      onClick={() => open({ kind: 'host', id: internet.gateway!.id, hint: { name: internet.gateway!.name, state: internet.gateway!.state } })}
                      className="inline-flex items-center gap-1.5 hover:text-accent"
                    >
                      <StatusDot status={hostState(internet.gateway.state)} size="sm" label="" />
                      {internet.gateway.name}
                    </button>,
                  ] as [string, ReactNode]]
                : []),
              ['Source', <span key="src" className="font-normal text-fg-2">{source}{latest?.server ? ` · ${latest.server}` : ''}</span>],
            ]}
          />
        </>
      )}
    </DashboardCard>
  );
}
