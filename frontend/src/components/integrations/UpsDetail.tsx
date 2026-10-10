'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import { KVList, KVRow, StatGrid, StatTile, UsageBar, fixed, isNum, tempClass, uptime, withUnit } from './parts';

interface UpsData {
  status: string;
  status_label: string;
  on_battery: boolean;
  battery_pct: number;
  runtime_s: number;
  load_pct: number;
  input_voltage: number;
  output_voltage: number;
  battery_voltage: number;
  temp: number | null;
  power_w: number | null;
  manufacturer: string;
  model: string;
  serial: string;
  firmware: string;
}

/** Battery charge: low is bad (≤ 20 down, ≤ 50 warning). */
function batteryState(pct: unknown): HealthState {
  if (!isNum(pct)) return 'unknown';
  if (pct <= 20) return 'down';
  if (pct <= 50) return 'warning';
  return 'ok';
}

export function UpsDetail({ data }: { data: UpsData }) {
  const power: HealthState = data.on_battery === true ? 'warning' : data.on_battery === false ? 'ok' : 'unknown';
  // On battery with a low charge is critical.
  const battery = batteryState(data.battery_pct);
  const banner: HealthState = power === 'warning' && battery === 'down' ? 'down' : power;

  return (
    <div className="space-y-6">
      {/* Status banner */}
      <Card padding="sm" glow={banner === 'down' ? 'crit' : banner === 'warning' ? 'warn' : undefined}>
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <StatusDot status={banner} size="lg" />
          <div className="min-w-0">
            <p className="truncate text-ui font-medium text-fg">{data.status_label || '—'}</p>
            <p className="text-meta text-fg-3">
              Status: <span className="font-mono">{data.status || '—'}</span>
            </p>
          </div>
          <div className="ml-auto">
            <StatusPill status={power}>
              {data.on_battery === true ? 'On battery' : data.on_battery === false ? 'On mains power' : undefined}
            </StatusPill>
          </div>
        </div>
      </Card>

      {/* Key metrics */}
      <StatGrid>
        <StatTile label="Battery" value={fixed(data.battery_pct, 0)} unit="%" state={battery === 'down' || battery === 'warning' ? battery : undefined} />
        <StatTile label="Runtime" value={data.runtime_s ? uptime(data.runtime_s) : null} />
        <StatTile label="Load" value={fixed(data.load_pct)} unit="%" />
        <StatTile
          label="Temperature"
          value={isNum(data.temp) ? <span className={cn(tempClass(data.temp, 40, 50))}>{data.temp}</span> : null}
          unit="°C"
        />
      </StatGrid>

      {/* Bars */}
      <Card as="section" className="space-y-4">
        <CardHeader title="Metrics" className="mb-0" />
        <UsageBar label="Battery" pct={data.battery_pct} state={battery} />
        <UsageBar label="Load" pct={data.load_pct} />
      </Card>

      {/* Voltage + device info */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card as="section">
          <CardHeader title="Electrical" />
          <KVList>
            <KVRow label="Input voltage">{withUnit(data.input_voltage, 'V')}</KVRow>
            <KVRow label="Output voltage">{withUnit(data.output_voltage, 'V')}</KVRow>
            <KVRow label="Battery voltage">{withUnit(data.battery_voltage, 'V')}</KVRow>
            {data.power_w != null && <KVRow label="Power">{withUnit(data.power_w, 'W', 0)}</KVRow>}
          </KVList>
        </Card>

        <Card as="section">
          <CardHeader title="Device info" />
          <KVList>
            <KVRow label="Manufacturer">{data.manufacturer}</KVRow>
            <KVRow label="Model">{data.model}</KVRow>
            <KVRow label="Serial" mono>{data.serial}</KVRow>
            <KVRow label="Firmware" mono>{data.firmware}</KVRow>
          </KVList>
        </Card>
      </div>
    </div>
  );
}
