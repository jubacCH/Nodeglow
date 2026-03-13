'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import Link from 'next/link';

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

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function PhpipamDetail({ data }: { data: PhpipamData }) {
  return (
    <div className="space-y-6">
      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Addresses" value={data.addresses_total} />
        <StatCard label="Active" value={data.addresses_active} />
        <StatCard label="Inactive" value={data.addresses_inactive} />
        <StatCard label="Subnets" value={data.subnets_count} />
      </div>

      {/* Address table */}
      {data.addresses && data.addresses.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Addresses ({data.addresses.length})</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">IP</th>
                  <th className="px-4 py-2 text-left">Hostname</th>
                  <th className="px-4 py-2 text-left">MAC</th>
                  <th className="px-4 py-2 text-left">Subnet</th>
                  <th className="px-4 py-2 text-right">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {data.addresses.map((a) => (
                  <tr key={`${a.ip}-${a.subnet_id}`} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 font-mono text-slate-200"><Link href={'/hosts?q=' + encodeURIComponent(a.ip)} className="text-sky-400 hover:underline">{a.ip}</Link></td>
                    <td className="px-4 py-2 text-slate-300">{a.hostname ? <Link href={'/hosts?q=' + encodeURIComponent(a.hostname)} className="text-sky-400 hover:underline">{a.hostname}</Link> : '—'}</td>
                    <td className="px-4 py-2 font-mono text-slate-400">{a.mac || '—'}</td>
                    <td className="px-4 py-2 text-slate-400">{a.subnet_id}</td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {a.last_seen ? new Date(a.last_seen).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
