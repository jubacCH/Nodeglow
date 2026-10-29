'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { BigNumber } from '@/components/ui/BigNumber';
import { KV, KVGrid, fixed, formatDate } from './parts';

interface SpeedtestData {
  download_mbps: number;
  upload_mbps: number;
  ping_ms: number | null;
  server_name: string;
  server_location: string;
  isp: string;
  timestamp: string;
}

function SpeedCard({ label, value, unit }: { label: string; value: number | null | undefined; unit: string }) {
  return (
    <Card padding="md">
      {/* null means the tool did not produce a usable reading. Showing "—"
          is honest; showing a number that was never measured is not. */}
      <BigNumber size="md" label={label} value={fixed(value)} unit={unit} />
    </Card>
  );
}

export function SpeedtestDetail({ data }: { data: SpeedtestData }) {
  return (
    <div className="space-y-6">
      {/* Speed cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <SpeedCard label="Download" value={data.download_mbps} unit="Mbps" />
        <SpeedCard label="Upload" value={data.upload_mbps} unit="Mbps" />
        <SpeedCard label="Ping" value={data.ping_ms} unit="ms" />
      </div>

      {/* Server info */}
      <Card as="section">
        <CardHeader title="Connection details" />
        <KVGrid>
          <KV label="Server">{data.server_name}</KV>
          <KV label="Location">{data.server_location}</KV>
          <KV label="ISP">{data.isp}</KV>
          <KV label="Tested">{formatDate(data.timestamp)}</KV>
        </KVGrid>
      </Card>
    </div>
  );
}
