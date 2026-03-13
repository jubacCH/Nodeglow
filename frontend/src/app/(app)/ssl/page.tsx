'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useHosts } from '@/hooks/queries/useHosts';
import { ShieldCheck } from 'lucide-react';
import Link from 'next/link';

export default function SslPage() {
  const { data: hosts, isLoading } = useHosts();
  const sslHosts = hosts?.filter((h) => h.check_type?.includes('https'));

  return (
    <div>
      <PageHeader title="SSL Certificates" description="Certificate expiry monitoring" />

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Hostname</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                  </tr>
                ))}
              {sslHosts?.map((h) => (
                <tr key={h.id} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-3">
                    <Link href={`/hosts/${h.id}`} className="flex items-center gap-2 text-slate-200 hover:text-sky-400">
                      <ShieldCheck size={14} className="text-emerald-400" />
                      {h.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-400">{h.hostname}</td>
                  <td className="px-4 py-3">
                    <Badge variant="severity" severity="info">HTTPS</Badge>
                  </td>
                </tr>
              ))}
              {!isLoading && !sslHosts?.length && (
                <tr>
                  <td colSpan={3} className="px-4 py-8 text-center text-sm text-slate-500">
                    No HTTPS hosts configured
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}
