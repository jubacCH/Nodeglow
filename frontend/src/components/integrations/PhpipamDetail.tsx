'use client';

import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import Link from 'next/link';
import { StatGrid, StatTile, TableCard, formatDate } from './parts';

interface PhpipamAddress {
  ip: string;
  hostname: string;
  last_seen: string;
  mac: string;
  subnet_id: number;
}

interface PhpipamData {
  addresses_total: number;
  addresses_active: number;
  addresses_inactive: number;
  subnets_count: number;
  addresses: PhpipamAddress[];
}

export function PhpipamDetail({ data }: { data: PhpipamData }) {
  return (
    <div className="space-y-6">
      {/* Summary stats */}
      <StatGrid>
        <StatTile label="Total addresses" value={data.addresses_total} />
        <StatTile label="Active" value={data.addresses_active} />
        <StatTile label="Inactive" value={data.addresses_inactive} />
        <StatTile label="Subnets" value={data.subnets_count} />
      </StatGrid>

      {/* Address table */}
      {data.addresses && data.addresses.length > 0 && (
        <TableCard title="Addresses" meta={`${data.addresses.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>IP</Th>
                <Th>Hostname</Th>
                <Th>MAC</Th>
                <Th numeric>Subnet</Th>
                <Th numeric>Last seen</Th>
              </Tr>
            </THead>
            <TBody>
              {data.addresses.map((a) => (
                <Tr key={`${a.ip}-${a.subnet_id}`}>
                  <Td className="whitespace-nowrap font-mono">
                    <Link href={'/hosts?q=' + encodeURIComponent(a.ip)} className="text-accent hover:underline">{a.ip}</Link>
                  </Td>
                  <Td className="max-w-[260px] truncate">
                    {a.hostname ? <Link href={'/hosts?q=' + encodeURIComponent(a.hostname)} className="text-accent hover:underline">{a.hostname}</Link> : <span className="text-fg-3">—</span>}
                  </Td>
                  <Td muted className="whitespace-nowrap font-mono">{a.mac || '—'}</Td>
                  <Td numeric muted>{a.subnet_id ?? '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap text-meta">{formatDate(a.last_seen) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
