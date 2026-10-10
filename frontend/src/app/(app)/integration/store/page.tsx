'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Plug, Search } from 'lucide-react';
import { get } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { buttonClasses } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Input } from '@/components/ui/Field';
import { StatusPill } from '@/components/ui/StatusPill';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import { sanitizeSvg } from '@/lib/sanitize';
import type { IntegrationConfig } from '@/types';
import { integrationHealth } from '../_components/health';

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

interface TypeHealth { problems: number; noData: number }

function summarize(instances: IntegrationConfig[] | undefined): Map<string, TypeHealth> {
  const out = new Map<string, TypeHealth>();
  const now = Date.now();
  for (const i of instances ?? []) {
    const h = integrationHealth(i, i.last_check, now);
    const t = out.get(i.type) ?? { problems: 0, noData: 0 };
    if (h.status === 'down') t.problems += 1;
    if (h.status === 'unknown') t.noData += 1;
    out.set(i.type, t);
  }
  return out;
}

export default function IntegrationStorePage() {
  useEffect(() => { document.title = 'Integrations | Nodeglow'; }, []);
  const [search, setSearch] = useState('');

  const query = useQuery<IntegrationMeta[]>({
    queryKey: ['integrations-store'],
    queryFn: () => get('/api/integrations'),
  });
  // Instance health per type (shared cache with the command palette).
  const { data: instances } = useIntegrations();
  const health = summarize(instances);

  const q = search.trim().toLowerCase();
  const matches = (i: IntegrationMeta) =>
    !q || i.display_name.toLowerCase().includes(q) || i.description.toLowerCase().includes(q) || i.name.toLowerCase().includes(q);

  return (
    <div>
      <PageHeader
        title="Integrations"
        description="Connect hypervisors, network gear, storage and services. Configured integrations show their collection health."
      />

      <div className="relative mb-5 max-w-md">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-3" aria-hidden="true" />
        <Input
          type="search"
          aria-label="Search integrations"
          placeholder="Search integrations…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
        />
      </div>

      <QueryState
        query={query}
        errorTitle="Could not load the integration catalogue"
        loading={
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Loading integrations">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <Skeleton className="mb-2 h-6 w-32" />
                <Skeleton className="mb-1 h-4 w-full" />
                <Skeleton className="h-4 w-2/3" />
              </Card>
            ))}
          </div>
        }
        empty={
          <Card>
            <EmptyState
              icon={Plug}
              title="No integrations available"
              description="The integration registry is empty — this usually means a backend startup error. Check the logs."
            />
          </Card>
        }
      >
        {(all) => {
          const filtered = all.filter(matches);
          const configured = filtered.filter((i) => i.configured > 0);
          const available = filtered.filter((i) => i.configured === 0);
          if (filtered.length === 0) {
            return (
              <Card>
                <EmptyState
                  variant="no-results"
                  icon={Search}
                  title="No matches"
                  description="Try a different search term, or clear the filter to browse the full catalogue."
                  action={<button type="button" className={buttonClasses({ variant: 'secondary', size: 'sm' })} onClick={() => setSearch('')}>Clear search</button>}
                />
              </Card>
            );
          }
          return (
            <>
              {configured.length > 0 && (
                <section aria-labelledby="int-configured" className="mb-8">
                  <h2 id="int-configured" className="mb-3 text-body font-medium text-fg">
                    Configured <span className="num text-fg-3">{configured.length}</span>
                  </h2>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {configured.map((int) => (
                      <IntegrationCard key={int.name} integration={int} health={health.get(int.name)} />
                    ))}
                  </div>
                </section>
              )}
              {available.length > 0 && (
                <section aria-labelledby="int-available">
                  <h2 id="int-available" className="mb-3 text-body font-medium text-fg">
                    Available <span className="num text-fg-3">{available.length}</span>
                  </h2>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {available.map((int) => (
                      <IntegrationCard key={int.name} integration={int} />
                    ))}
                  </div>
                </section>
              )}
            </>
          );
        }}
      </QueryState>
    </div>
  );
}

function IntegrationCard({ integration: int, health }: { integration: IntegrationMeta; health?: TypeHealth }) {
  const isConfigured = int.configured > 0;
  const titleId = `int-${int.name}`;

  return (
    <Card as="article" aria-labelledby={titleId} interactive className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          {int.icon_svg ? (
            <div
              aria-hidden="true"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-ctl bg-surface-2 text-fg-2 [&_svg]:h-5 [&_svg]:w-5"
              dangerouslySetInnerHTML={{ __html: sanitizeSvg(int.icon_svg) }}
            />
          ) : (
            <div aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-ctl bg-surface-2 text-ui font-semibold text-fg-2">
              {int.display_name[0]}
            </div>
          )}
          <div className="min-w-0">
            <h3 id={titleId} className="truncate text-body font-medium text-fg">{int.display_name}</h3>
            <span className="font-mono text-micro text-fg-3">{int.name}</span>
          </div>
        </div>
        {isConfigured && (
          <div className="flex shrink-0 flex-col items-end gap-1">
            {health && health.problems > 0 ? (
              <StatusPill status="down" size="sm">{health.problems} failing</StatusPill>
            ) : health && health.noData > 0 ? (
              <StatusPill status="unknown" size="sm">{health.noData} no data</StatusPill>
            ) : null}
            <span className="text-meta text-fg-2"><span className="num">{int.configured}</span> configured</span>
          </div>
        )}
      </div>

      <p className="flex-1 text-ui leading-relaxed text-fg-2">
        {int.description || 'No description available.'}
      </p>

      <div className="flex items-center justify-end pt-1">
        <Link
          href={`/integration/${int.name}`}
          className={buttonClasses({ variant: 'secondary', size: 'sm' })}
          aria-label={`${isConfigured ? 'Manage' : 'Configure'} ${int.display_name}`}
        >
          {isConfigured ? 'Manage' : 'Configure'}
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      </div>
    </Card>
  );
}
