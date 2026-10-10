'use client';

import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { BigNumber } from '@/components/ui/BigNumber';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import type { HealthState } from '@/lib/status';
import Link from 'next/link';
import { KV, KVGrid, SectionTitle, StatGrid, StatTile, TableCard, UsageBar, fixed, formatBytes, isNum } from './parts';

interface UnifiPort {
  idx: number;
  name?: string;
  up: boolean;
  speed: number;
  speed_label?: string;
  is_uplink?: boolean;
  poe_enable?: boolean;
  poe_power?: number;
  rx_bytes_r?: number;
  tx_bytes_r?: number;
}

interface UnifiDevice {
  name: string;
  model: string;
  mac: string;
  ip: string;
  type_label: string;
  state: number;
  version: string;
  cpu_pct: number;
  mem_pct: number;
  clients_wifi: number;
  clients_wired: number;
  rx_bytes: number;
  tx_bytes: number;
  satisfaction: number;
  has_ports: boolean;
  port_table?: UnifiPort[];
}

interface SpeedtestResult {
  timestamp: string;
  download_mbps: number;
  upload_mbps: number;
  latency_ms: number;
}

interface UnifiData {
  devices: UnifiDevice[];
  speedtest?: SpeedtestResult[];
  speedtest_latest?: SpeedtestResult | null;
}

/** UniFi device state: 1 = connected, 0 = disconnected; anything else is shown as unknown. */
function deviceState(state: number | null | undefined): { status: HealthState; label: string } {
  if (state === 1) return { status: 'ok', label: 'Connected' };
  if (state === 0) return { status: 'down', label: 'Disconnected' };
  return { status: 'unknown', label: isNum(state) ? `State ${state}` : 'No data' };
}

function SpeedtestChart({ results, latest }: { results: SpeedtestResult[]; latest: SpeedtestResult }) {
  const t = useChartTheme();
  const ordered = [...results].reverse();
  return (
    <Card padding="sm">
      <EChart
        height={220}
        ariaLabel={`Gateway speedtest history, ${results.length} runs: download, upload and latency`}
        option={{
          tooltip: { trigger: 'axis' },
          legend: { data: ['Download', 'Upload', 'Latency'] },
          xAxis: {
            type: 'category',
            data: ordered.map((s) => s.timestamp),
          },
          yAxis: [
            { type: 'value', name: 'Mbps', axisLabel: { formatter: '{value}' } },
            { type: 'value', name: 'ms', axisLabel: { formatter: '{value}' } },
          ],
          series: [
            {
              name: 'Download',
              type: 'line',
              data: ordered.map((s) => s.download_mbps),
              color: t.series[0],
              smooth: true,
              areaStyle: { color: t.accentFill },
            },
            {
              name: 'Upload',
              type: 'line',
              data: ordered.map((s) => s.upload_mbps),
              color: t.series[1],
              smooth: true,
            },
            {
              name: 'Latency',
              type: 'line',
              yAxisIndex: 1,
              data: ordered.map((s) => s.latency_ms),
              color: t.series[2],
              smooth: true,
            },
          ],
        }}
      />
      {latest.timestamp && <p className="mt-2 text-right text-micro text-fg-3">Last test: {latest.timestamp}</p>}
    </Card>
  );
}

export function UnifiDetail({ data }: { data: UnifiData }) {
  const devices = data.devices ?? [];
  const totalWifi = devices.reduce((sum, d) => sum + (d.clients_wifi ?? 0), 0);
  const totalWired = devices.reduce((sum, d) => sum + (d.clients_wired ?? 0), 0);
  const latest = data.speedtest_latest;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <StatGrid>
        <StatTile label="Devices" value={devices.length} />
        <StatTile label="WiFi clients" value={totalWifi} />
        <StatTile label="Wired clients" value={totalWired} />
        <StatTile label="Total clients" value={totalWifi + totalWired} />
      </StatGrid>

      {/* Speedtest */}
      {latest && (
        <section>
          <SectionTitle>Gateway speedtest</SectionTitle>
          <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card padding="sm"><BigNumber size="sm" label="Download" value={fixed(latest.download_mbps)} unit="Mbps" /></Card>
            <Card padding="sm"><BigNumber size="sm" label="Upload" value={fixed(latest.upload_mbps)} unit="Mbps" /></Card>
            <Card padding="sm"><BigNumber size="sm" label="Latency" value={isNum(latest.latency_ms) ? latest.latency_ms : null} unit="ms" /></Card>
          </div>
          {data.speedtest && data.speedtest.length > 1 && <SpeedtestChart results={data.speedtest} latest={latest} />}
        </section>
      )}

      {/* Device cards */}
      {devices.length > 0 && (
        <section>
          <SectionTitle>Devices</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {devices.map((d) => {
              const st = deviceState(d.state);
              return (
                <Card key={d.mac} padding="sm" className="space-y-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <StatusDot status={st.status} label={st.label} />
                    <span className="truncate text-ui font-medium text-fg">{d.name || d.mac}</span>
                    <Badge className="ml-auto">{d.type_label}</Badge>
                  </div>
                  <KVGrid className="grid-cols-2 gap-y-2 md:grid-cols-2">
                    <KV label="Model">{d.model}</KV>
                    <KV label="IP" mono>
                      {d.ip && <Link href={'/hosts?q=' + encodeURIComponent(d.ip)} className="text-accent hover:underline">{d.ip}</Link>}
                    </KV>
                    <KV label="Version" mono>{d.version}</KV>
                    <KV label="Satisfaction">{isNum(d.satisfaction) && d.satisfaction >= 0 ? `${d.satisfaction} %` : null}</KV>
                    <KV label="WiFi clients">{isNum(d.clients_wifi) ? d.clients_wifi : null}</KV>
                    <KV label="Wired clients">{isNum(d.clients_wired) ? d.clients_wired : null}</KV>
                    <KV label="RX">{formatBytes(d.rx_bytes)}</KV>
                    <KV label="TX">{formatBytes(d.tx_bytes)}</KV>
                  </KVGrid>
                  <div className="space-y-2">
                    <UsageBar label="CPU" pct={d.cpu_pct} />
                    <UsageBar label="Memory" pct={d.mem_pct} />
                  </div>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      {/* Port tables for switches */}
      {devices.filter((d) => d.has_ports && d.port_table && d.port_table.length > 0).map((d) => (
        <TableCard key={`ports-${d.mac}`} title={`Ports — ${d.name || d.mac}`}>
          <Table density="compact">
            <THead>
              <Tr>
                <Th>Link</Th>
                <Th numeric>#</Th>
                <Th>Name</Th>
                <Th>Speed</Th>
                <Th numeric>PoE</Th>
                <Th numeric>RX</Th>
                <Th numeric>TX</Th>
              </Tr>
            </THead>
            <TBody>
              {d.port_table!.map((p) => (
                <Tr key={p.idx}>
                  <Td>
                    <StatusDot
                      status={p.up === true ? 'ok' : p.up === false ? 'disabled' : 'unknown'}
                      label={p.up === true ? 'Link up' : p.up === false ? 'No link' : 'No data'}
                    />
                  </Td>
                  <Td numeric>{p.idx}</Td>
                  <Td muted className="whitespace-nowrap">
                    {p.name || '—'}
                    {p.is_uplink && <Badge tone="accent" className="ml-2">Uplink</Badge>}
                  </Td>
                  <Td muted className="num whitespace-nowrap">
                    {p.up ? (p.speed_label || (p.speed ? `${p.speed}M` : '—')) : '—'}
                  </Td>
                  <Td numeric muted>
                    {p.poe_enable ? (isNum(p.poe_power) && p.poe_power ? `${p.poe_power.toFixed(1)} W` : 'On') : '—'}
                  </Td>
                  <Td numeric muted className="whitespace-nowrap">
                    {p.up && p.rx_bytes_r ? formatBytes(p.rx_bytes_r) + '/s' : '—'}
                  </Td>
                  <Td numeric muted className="whitespace-nowrap">
                    {p.up && p.tx_bytes_r ? formatBytes(p.tx_bytes_r) + '/s' : '—'}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      ))}
    </div>
  );
}
