'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Search, ExternalLink, CheckCircle, Plug } from 'lucide-react';
import { EmptyState } from '@/components/ui/EmptyState';

interface IntegrationMeta {
  name: string;
  display_name: string;
  icon: string;
  icon_svg: string;
  color: string;
  description: string;
  single_instance: boolean;
  configured: number;
}

export default function IntegrationStorePage() {
  useEffect(() => { document.title = 'Integration Store | Nodeglow'; }, []);
  const router = useRouter();
  const [search, setSearch] = useState('');

  const { data: integrations, isLoading } = useQuery<IntegrationMeta[]>({
    queryKey: ['integrations-store'],
    queryFn: () => get('/api/integrations'),
  });

  const filtered = integrations?.filter((i) =>
    i.display_name.toLowerCase().includes(search.toLowerCase()) ||
    i.description.toLowerCase().includes(search.toLowerCase()) ||
    i.name.toLowerCase().includes(search.toLowerCase())
  );

  const configured = filtered?.filter((i) => i.configured > 0) ?? [];
  const available = filtered?.filter((i) => i.configured === 0) ?? [];

  return (
    <div>
      <PageHeader
        title="Integration Store"
        description="Browse and configure integrations for your infrastructure"
      />

      {/* Search */}
      <div className="mb-6 relative max-w-md">
        <Search size={16} className="absolute left-3 top-2.5 text-slate-500" />
        <input
          type="text"
          placeholder="Search integrations..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full pl-10 pr-4 py-2 text-sm rounded-lg bg-[var(--ng-glass-bg)] border border-[var(--ng-glass-border)] text-[var(--ng-text-primary)] placeholder:text-[var(--ng-text-muted)] focus:outline-none focus:ring-2 focus:ring-sky-500/50"
        />
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <GlassCard key={i} className="p-5">
              <Skeleton className="h-6 w-32 mb-2" />
              <Skeleton className="h-4 w-full mb-1" />
              <Skeleton className="h-4 w-2/3" />
            </GlassCard>
          ))}
        </div>
      )}

      {/* Configured integrations */}
      {configured.length > 0 && (
        <>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--ng-text-muted)] mb-3">
            Configured ({configured.length})
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
            {configured.map((int) => (
              <IntegrationCard key={int.name} integration={int} onConfigure={() => router.push(`/integration/${int.name}`)} />
            ))}
          </div>
        </>
      )}

      {/* Available integrations */}
      {available.length > 0 && (
        <>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--ng-text-muted)] mb-3">
            Available ({available.length})
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {available.map((int) => (
              <IntegrationCard key={int.name} integration={int} onConfigure={() => router.push(`/integration/${int.name}`)} />
            ))}
          </div>
        </>
      )}

      {filtered?.length === 0 && !isLoading && (
        <GlassCard>
          <EmptyState
            icon={Plug}
            title={search ? 'No matches' : 'No integrations available'}
            description={
              search
                ? 'Try a different search term, or browse the full catalogue with an empty filter.'
                : 'The integration registry is empty — this usually means a backend startup error. Check the logs.'
            }
          />
        </GlassCard>
      )}
    </div>
  );
}

function IntegrationCard({ integration: int, onConfigure }: { integration: IntegrationMeta; onConfigure: () => void }) {
  const isConfigured = int.configured > 0;

  return (
    <GlassCard className="p-5 flex flex-col gap-3 hover:border-sky-500/20 transition-colors group">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {int.icon_svg ? (
            <div className="w-9 h-9 rounded-lg bg-white/[0.06] flex items-center justify-center" dangerouslySetInnerHTML={{ __html: int.icon_svg }} />
          ) : (
            <div className="w-9 h-9 rounded-lg bg-white/[0.06] flex items-center justify-center text-sm font-bold text-[var(--ng-text-secondary)]">
              {int.display_name[0]}
            </div>
          )}
          <div>
            <h3 className="text-sm font-semibold text-[var(--ng-text-primary)]">{int.display_name}</h3>
            <span className="text-[10px] text-[var(--ng-text-muted)] font-mono">{int.name}</span>
          </div>
        </div>
        {isConfigured && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle size={10} />
            {int.configured} active
          </span>
        )}
      </div>

      <p className="text-xs text-[var(--ng-text-secondary)] leading-relaxed flex-1">
        {int.description || 'No description available.'}
      </p>

      <div className="flex items-center justify-end pt-1">
        <Button size="sm" variant={isConfigured ? 'ghost' : 'primary'} onClick={onConfigure}>
          <ExternalLink size={12} />
          {isConfigured ? 'Manage' : 'Configure'}
        </Button>
      </div>
    </GlassCard>
  );
}
