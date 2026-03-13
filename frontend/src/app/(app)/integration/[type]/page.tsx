'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import Link from 'next/link';

export default function IntegrationListPage() {
  const params = useParams();
  const type = params.type as string;
  const { data: integrations, isLoading } = useIntegrations(type);

  return (
    <div>
      <PageHeader
        title={type.charAt(0).toUpperCase() + type.slice(1)}
        description={`Integration instances for ${type}`}
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <GlassCard key={i} className="p-4">
              <Skeleton className="h-5 w-40 mb-3" />
              <Skeleton className="h-4 w-24 mb-2" />
              <Skeleton className="h-4 w-32" />
            </GlassCard>
          ))}
        {integrations?.map((int) => (
          <Link key={int.id} href={`/integration/${type}/${int.id}`}>
            <GlassCard className="p-4 hover:bg-white/[0.06] transition-colors cursor-pointer">
              <div className="flex items-center gap-3 mb-3">
                <StatusDot status={int.enabled ? 'online' : 'disabled'} />
                <p className="text-sm font-medium text-slate-200">{int.name}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge>{int.type}</Badge>
                {!int.enabled && <Badge variant="severity" severity="warning">Disabled</Badge>}
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Created: {new Date(int.created_at).toLocaleDateString()}
              </p>
            </GlassCard>
          </Link>
        ))}
        {!isLoading && (!integrations || integrations.length === 0) && (
          <GlassCard className="p-8 col-span-full">
            <p className="text-center text-sm text-slate-500">No {type} integrations configured</p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}
