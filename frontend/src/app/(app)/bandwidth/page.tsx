'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpDown, Network } from 'lucide-react';
import type { EChartsOption } from 'echarts';
import { get } from '@/lib/api';
import { EChart } from '@/components/charts/LazyEChart';
import { PageHeader } from '@/components/layout/PageHeader';
import { BigNumber } from '@/components/ui/BigNumber';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { SegmentedControl } from '@/components/ui/Tabs';
import { TBody, THead, Table, TableContainer, Td, Th, Tr } from '@/components/ui/Table';
import { useChartTheme } from '@/lib/chart-theme';
import { cn } from '@/lib/utils';

/* ---------- helpers ---------- */

/** Rate with unit. Missing values are "—", never "0 bps". */
function splitBps(bps: number | undefined | null): { value: string; unit: string } | null {
  if (bps === null || bps === undefined || Number.isNaN(bps)) return null;
  if (bps <= 0) return { value: '0', unit: 'bps' };
  if (bps >= 1e9) return { value: (bps / 1e9).toFixed(2), unit: 'Gbps' };
  if (bps >= 1e6) return { value: (bps / 1e6).toFixed(1), unit: 'Mbps' };
  if (bps >= 1e3) return { value: (bps / 1e3).toFixed(0), unit: 'Kbps' };
  return { value: String(Math.round(bps)), unit: 'bps' };
}

function formatBps(bps: number | undefined | null): string {
  const s = splitBps(bps);
  return s ? `${s.value} ${s.unit}` : '—';
}

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  if (isNaN(diff)) return '—';
  if (diff < 60_000) return 'just now';
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}h ago`;
  return `${Math.floor(diff / 86400_000)}d ago`;
}

/** An interface counts as reporting when its last sample is younger than this. */
const FRESH_MS = 300_000;

const HOUR_OPTIONS = [
  { label: '1h', value: 1, long: 'last hour' },
  { label: '6h', value: 6, long: 'last 6 hours' },
  { label: '24h', value: 24, long: 'last 24 hours' },
  { label: '7d', value: 168, long: 'last 7 days' },
] as const;

/* ---------- types ---------- */

interface BandwidthSummary {
  total_rx_bps?: number;
  total_tx_bps?: number;
  total_interfaces?: number;
  top_talkers?: Array<{
    source_type?: string;
    source_id?: string;
    source_name?: string;
    interface_name?: string;
    rx_rate_bps?: number;
    tx_rate_bps?: number;
  }>;
  by_source?: Array<{
    source_type?: string;
    total_rx_bps?: number;
    total_tx_bps?: number;
  }>;
}

interface BandwidthInterface {
  source_type?: string;
  source_id?: string;
  interface_name?: string;
  display_name?: string;
  rx_rate_bps?: number;
  tx_rate_bps?: number;
  last_seen?: string;
}

interface HistoryPoint {
  timestamp?: string;
  rx_rate_bps?: number;
  tx_rate_bps?: number;
}

function talkerLabel(t: NonNullable<BandwidthSummary['top_talkers']>[number]): string {
  let name = (t.interface_name ?? '').replace(/^device\//, '');
  // A MAC-like interface name says nothing; prefer the source name.
  if (/^[0-9A-Fa-f]{12}/.test(name) && t.source_name) name = t.source_name;
  if (name.length > 25) name = name.slice(0, 22) + '…';
  return name || t.source_name || '—';
}

/** "device / interface" so equal interface names (eth0 on two hosts) stay distinguishable. */
function talkerFullLabel(t: NonNullable<BandwidthSummary['top_talkers']>[number]): string {
  const iface = talkerLabel(t);
  if (!t.source_name || iface === t.source_name) return iface;
  const full = `${t.source_name} / ${iface}`;
  return full.length > 32 ? full.slice(0, 31) + '…' : full;
}

/* ---------- component ---------- */

export default function BandwidthPage() {
  useEffect(() => { document.title = 'Traffic | Nodeglow'; }, []);

  const [hours, setHours] = useState(24);
  const t = useChartTheme();
  const range = HOUR_OPTIONS.find((o) => o.value === hours) ?? HOUR_OPTIONS[2];

  const summaryQ = useQuery({
    queryKey: ['bandwidth-summary'],
    queryFn: () => get<BandwidthSummary>('/api/bandwidth'),
    refetchInterval: 15_000,
  });
  const interfacesQ = useQuery({
    queryKey: ['bandwidth-interfaces'],
    queryFn: () => get<BandwidthInterface[]>('/api/bandwidth/interfaces'),
    refetchInterval: 15_000,
  });
  const historyQ = useQuery({
    queryKey: ['bandwidth-history', hours],
    queryFn: () => get<HistoryPoint[]>(`/api/bandwidth/history?hours=${hours}`),
    refetchInterval: 15_000,
  });

  const summary = summaryQ.data;
  const interfaces = interfacesQ.data;
  const history = historyQ.data;

  const sortedInterfaces = useMemo(() => {
    if (!interfaces || !Array.isArray(interfaces)) return [];
    return [...interfaces].sort(
      (a, b) => ((b.rx_rate_bps ?? 0) + (b.tx_rate_bps ?? 0)) - ((a.rx_rate_bps ?? 0) + (a.tx_rate_bps ?? 0)),
    );
  }, [interfaces]);

  const reporting = useMemo(
    () => sortedInterfaces.filter((i) => i.last_seen && Date.now() - new Date(i.last_seen).getTime() < FRESH_MS).length,
    [sortedInterfaces],
  );

  const topTalker = summary?.top_talkers?.[0];
  const hasHistory = Array.isArray(history) && history.length > 0;
  const talkers = useMemo(() => summary?.top_talkers?.slice(0, 10) ?? [], [summary]);

  /* --- chart options --- */

  const trafficOption = useMemo((): EChartsOption => {
    if (!Array.isArray(history) || history.length === 0) return {};
    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => `${v} Mbps`,
      },
      legend: { data: ['Download', 'Upload'], top: 0, right: 0 },
      grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: history.map((p) => p.timestamp ?? ''),
        axisLabel: {
          formatter: (v: string) => {
            const d = new Date(v);
            if (Number.isNaN(d.getTime())) return v;
            return hours <= 6
              ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
              : d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
          },
        },
      },
      yAxis: { type: 'value', name: 'Mbps', nameTextStyle: { color: t.text3, align: 'left' } },
      series: [
        {
          name: 'Download', type: 'line', showSymbol: false,
          lineStyle: { width: 2, color: t.series[0] }, itemStyle: { color: t.series[0] },
          areaStyle: { color: t.accentFill, opacity: 1 },
          data: history.map((p) => +((p.rx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
        {
          name: 'Upload', type: 'line', showSymbol: false,
          lineStyle: { width: 2, color: t.series[1] }, itemStyle: { color: t.series[1] },
          data: history.map((p) => +((p.tx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
      ],
    };
  }, [history, hours, t]);

  const topTalkersOption = useMemo((): EChartsOption => {
    if (talkers.length === 0) return {};
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        valueFormatter: (v) => `${typeof v === 'number' ? v.toFixed(1) : v} Mbps`,
      },
      legend: { data: ['Download', 'Upload'], top: 0, right: 0 },
      grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}G` : `${v}M`) },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: talkers.map(talkerFullLabel),
        axisLabel: { width: 190, overflow: 'truncate', fontFamily: t.monoFamily },
      },
      series: [
        {
          name: 'Download', type: 'bar', stack: 'total', barMaxWidth: 18, itemStyle: { color: t.series[0] },
          data: talkers.map((x) => +((x.rx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
        {
          name: 'Upload', type: 'bar', stack: 'total', barMaxWidth: 18,
          itemStyle: { color: t.series[1], borderRadius: [0, 3, 3, 0] },
          data: talkers.map((x) => +((x.tx_rate_bps ?? 0) / 1e6).toFixed(2)),
        },
      ],
    };
  }, [talkers, t]);

  const rx = splitBps(summary?.total_rx_bps);
  const tx = splitBps(summary?.total_tx_bps);
  const top = topTalker ? splitBps((topTalker.rx_rate_bps ?? 0) + (topTalker.tx_rate_bps ?? 0)) : null;
  const asOf = formatAsOf(summaryQ.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Traffic"
        description={<>Bandwidth per interface from agents and integrations{asOf && <> · Updated {asOf}</>}</>}
      />

      {summaryQ.isError && summary && (
        <StaleDataBanner error={summaryQ.error} onRetry={summaryQ.refetch} updatedAt={summaryQ.dataUpdatedAt} />
      )}

      {/* Key figures */}
      {summaryQ.isError && !summary ? (
        <Card className="mb-4">
          <QueryErrorState compact error={summaryQ.error} onRetry={summaryQ.refetch} title="Could not load the traffic summary" />
        </Card>
      ) : (
        <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card>
            {summaryQ.isLoading ? <Skeleton className="h-[52px] w-28" /> : (
              <BigNumber size="sm" value={rx?.value ?? null} unit={rx?.unit} label="Total download" />
            )}
          </Card>
          <Card>
            {summaryQ.isLoading ? <Skeleton className="h-[52px] w-28" /> : (
              <BigNumber size="sm" value={tx?.value ?? null} unit={tx?.unit} label="Total upload" />
            )}
          </Card>
          <Card>
            {summaryQ.isLoading ? <Skeleton className="h-[52px] w-16" /> : (
              <BigNumber
                size="sm"
                value={summary?.total_interfaces ?? (interfaces ? sortedInterfaces.length : null)}
                label={interfaces ? `Interfaces · ${reporting} reporting` : 'Interfaces'}
              />
            )}
          </Card>
          <Card>
            {summaryQ.isLoading ? <Skeleton className="h-[52px] w-28" /> : (
              <BigNumber
                size="sm"
                value={top?.value ?? null}
                unit={top?.unit}
                label={
                  <span className="block truncate" title={topTalker ? `${topTalker.source_name ?? ''} ${topTalker.interface_name ?? ''}`.trim() : undefined}>
                    Top talker{topTalker && <>: <span className="font-mono">{talkerFullLabel(topTalker)}</span></>}
                  </span>
                }
              />
            )}
          </Card>
        </div>
      )}

      {/* Traffic chart */}
      <Card as="section" aria-labelledby="h-traffic" className="mb-4">
        <CardHeader
          title="Traffic"
          titleId="h-traffic"
          meta={range.long}
          actions={
            <SegmentedControl
              label="Time range"
              size="sm"
              value={String(hours)}
              onChange={(v) => setHours(Number(v))}
              options={HOUR_OPTIONS.map((o) => ({ value: String(o.value), label: o.label, ariaLabel: o.long }))}
            />
          }
        />
        {historyQ.isLoading ? (
          <Skeleton className="h-[300px] w-full" />
        ) : historyQ.isError && !history ? (
          <QueryErrorState compact error={historyQ.error} onRetry={historyQ.refetch} title="Could not load traffic history" />
        ) : hasHistory ? (
          <>
            {historyQ.isError && <StaleDataBanner error={historyQ.error} onRetry={historyQ.refetch} updatedAt={historyQ.dataUpdatedAt} />}
            <EChart
              option={trafficOption}
              height={300}
              ariaLabel={`Total download and upload in Mbps over the ${range.long}.`}
            />
          </>
        ) : (
          <div className="grid min-h-[300px] place-items-center">
            <EmptyState
              compact
              variant="not-configured"
              icon={ArrowUpDown}
              title="No traffic data yet"
              description="Data appears after agent or integration snapshots with interface counters are collected."
            />
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        {/* Interfaces */}
        <Card as="section" padding="none" aria-labelledby="h-ifaces" className="overflow-hidden">
          <div className="px-5 pt-5 max-[759px]:px-4 max-[759px]:pt-4">
            <CardHeader
              title="Interfaces"
              titleId="h-ifaces"
              meta={interfaces ? `${sortedInterfaces.length} · sorted by total rate` : undefined}
              className="mb-3"
            />
          </div>
          {interfacesQ.isError && interfaces && (
            <div className="px-5 max-[759px]:px-4">
              <StaleDataBanner error={interfacesQ.error} onRetry={interfacesQ.refetch} updatedAt={interfacesQ.dataUpdatedAt} />
            </div>
          )}
          {interfacesQ.isError && !interfaces ? (
            <QueryErrorState compact error={interfacesQ.error} onRetry={interfacesQ.refetch} title="Could not load interfaces" />
          ) : !interfacesQ.isLoading && sortedInterfaces.length === 0 ? (
            <EmptyState compact variant="not-configured" icon={Network} title="No interfaces reporting yet" description="Interfaces appear once an agent or integration sends interface counters." />
          ) : (
            <TableContainer maxHeight={420}>
              <Table density="compact" aria-label="Interfaces">
                <THead sticky>
                  <Tr>
                    <Th>Interface</Th>
                    <Th numeric>Download</Th>
                    <Th numeric>Upload</Th>
                    <Th numeric>Last seen</Th>
                  </Tr>
                </THead>
                <TBody>
                  {interfacesQ.isLoading && Array.from({ length: 5 }).map((_, i) => (
                    <Tr key={i}>
                      <Td><Skeleton className="h-3.5 w-40" /></Td>
                      <Td><Skeleton className="ml-auto h-3.5 w-16" /></Td>
                      <Td><Skeleton className="ml-auto h-3.5 w-16" /></Td>
                      <Td><Skeleton className="ml-auto h-3.5 w-12" /></Td>
                    </Tr>
                  ))}
                  {sortedInterfaces.map((iface) => {
                    const fresh = iface.last_seen
                      ? Date.now() - new Date(iface.last_seen).getTime() < FRESH_MS
                      : false;
                    const name = iface.display_name ?? `${iface.source_type ?? '?'}/${iface.interface_name ?? '?'}`;
                    return (
                      <Tr key={`${iface.source_type}|${iface.source_id}|${iface.interface_name}`} className="hover:bg-hover">
                        <Td className="max-w-[260px]">
                          <span className="flex min-w-0 items-center gap-2">
                            {/* Fresh sample = ok; stale or never seen = no data (never green). */}
                            <StatusDot status={fresh ? 'ok' : 'unknown'} size="sm" label={fresh ? 'Reporting' : 'No recent data'} />
                            <span className="truncate font-mono text-meta" title={name}>{name}</span>
                          </span>
                        </Td>
                        <Td numeric className={cn('whitespace-nowrap', !fresh && 'text-fg-3')}>{formatBps(iface.rx_rate_bps)}</Td>
                        <Td numeric className={cn('whitespace-nowrap', !fresh && 'text-fg-3')}>{formatBps(iface.tx_rate_bps)}</Td>
                        <Td numeric muted className="whitespace-nowrap" title={iface.last_seen ? new Date(iface.last_seen).toLocaleString() : undefined}>
                          {timeAgo(iface.last_seen)}
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </Card>

        {/* Top talkers */}
        <Card as="section" aria-labelledby="h-talkers">
          <CardHeader title="Top talkers" titleId="h-talkers" meta="current rate · top 10" />
          {summaryQ.isLoading ? (
            <Skeleton className="h-[400px] w-full" />
          ) : summaryQ.isError && !summary ? (
            <QueryErrorState compact error={summaryQ.error} onRetry={summaryQ.refetch} title="Could not load top talkers" />
          ) : talkers.length > 0 ? (
            <EChart
              option={topTalkersOption}
              height={400}
              ariaLabel={`Top talkers by current rate: ${talkers.map((x) => `${talkerFullLabel(x)} ${formatBps((x.rx_rate_bps ?? 0) + (x.tx_rate_bps ?? 0))}`).join(', ')}.`}
            />
          ) : (
            <div className="grid min-h-[400px] place-items-center">
              <EmptyState compact variant="not-configured" icon={ArrowUpDown} title="No top talkers yet" description="Shown once interfaces report traffic." />
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
