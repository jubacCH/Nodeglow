'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Globe, Key, Lock, RefreshCw, ShieldCheck } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button, buttonClasses } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { BigNumber } from '@/components/ui/BigNumber';
import { StatusPill } from '@/components/ui/StatusPill';
import { Tag } from '@/components/ui/Tag';
import { SidePanel, PanelSection } from '@/components/ui/SidePanel';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, QueryErrorState, formatAsOf } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { ExportButton } from '@/components/ui/ExportButton';
import { get, post, apiErrorMessage } from '@/lib/api';
import type { HealthState } from '@/lib/status';
import { useToastStore } from '@/stores/toast';

interface SslCert {
  id: number | null;
  name: string;
  hostname: string;
  enabled: boolean;
  days: number | null;
  source?: string;
  source_label?: string;
  provider?: string;
}

interface SslData {
  certs: SslCert[];
  expiring_soon: number;
}

interface SslDetail {
  ok: boolean;
  error?: string;
  days?: number;
  expiry_date?: string;
  issued_date?: string;
  issuer?: string;
  issuer_cn?: string;
  issuer_o?: string;
  subject?: string;
  subject_cn?: string;
  subject_o?: string;
  sans?: string[];
  serial?: string;
  fingerprint?: string;
  signature_algorithm?: string;
  key_size?: number;
  port?: number;
}

/** Expiry thresholds: ≤ 7 days (or expired) is down, ≤ 30 days a warning. */
function expiryState(c: Pick<SslCert, 'days' | 'enabled'>): { status: HealthState | 'disabled'; label: string } {
  if (c.enabled === false) return { status: 'disabled', label: 'Paused' };
  if (c.days === null || c.days === undefined) return { status: 'unknown', label: 'No data' };
  if (c.days < 0) return { status: 'down', label: 'Expired' };
  if (c.days <= 7) return { status: 'down', label: '≤ 7 days' };
  if (c.days <= 30) return { status: 'warning', label: '≤ 30 days' };
  return { status: 'ok', label: 'Valid' };
}

function daysText(days: number | null) {
  if (days === null) return '—';
  if (days < 0) return `${Math.abs(days)} d ago`;
  return `${days} d`;
}

type SortDir = 'asc' | 'desc';

export default function SslPage() {
  useEffect(() => { document.title = 'Certificates | Nodeglow'; }, []);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<SslCert | null>(null);
  const [sort, setSort] = useState<SortDir>('asc');
  const query = useQuery({
    queryKey: ['ssl-certs'],
    queryFn: () => get<SslData>('/api/ssl/certs'),
  });
  const { data } = query;

  const certs = useMemo(() => data?.certs ?? [], [data]);
  // Most urgent first; certificates without data at the end either way.
  const sorted = useMemo(() => {
    const dir = sort === 'asc' ? 1 : -1;
    return [...certs].sort((a, b) => {
      if (a.days === null && b.days === null) return a.name.localeCompare(b.name);
      if (a.days === null) return 1;
      if (b.days === null) return -1;
      return (a.days - b.days) * dir;
    });
  }, [certs, sort]);

  const counts = useMemo(() => {
    const c = { critical: 0, warning: 0, ok: 0, unknown: 0 };
    for (const cert of certs) {
      const s = expiryState(cert).status;
      if (s === 'down') c.critical += 1;
      else if (s === 'warning') c.warning += 1;
      else if (s === 'ok') c.ok += 1;
      else c.unknown += 1;
    }
    return c;
  }, [certs]);

  async function refreshAll() {
    setRefreshing(true);
    try {
      await post('/api/ssl/refresh-all');
      qc.invalidateQueries({ queryKey: ['ssl-certs'] });
    } catch (e) {
      toast(apiErrorMessage(e, 'Failed to refresh certificates'), 'error');
    } finally {
      setRefreshing(false);
    }
  }

  const asOf = formatAsOf(query.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Certificates"
        description={`TLS certificate expiry for HTTPS hosts and integrations${asOf ? ` · checked ${asOf}` : ''}`}
        actions={
          <>
            {certs.length > 0 && (
              <ExportButton
                data={certs.map(c => ({ name: c.name, hostname: c.hostname, days_until_expiry: c.days }))}
                filename="ssl-certificates"
                columns={[
                  { key: 'name', label: 'Name' },
                  { key: 'hostname', label: 'Hostname' },
                  { key: 'days_until_expiry', label: 'Days Until Expiry' },
                ]}
              />
            )}
            <Button variant="secondary" size="sm" onClick={refreshAll} loading={refreshing}>
              {!refreshing && <RefreshCw size={14} aria-hidden="true" />}
              {refreshing ? 'Refreshing…' : 'Refresh all'}
            </Button>
          </>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card padding="sm">
          <BigNumber size="sm" value={data ? counts.critical : undefined} state={counts.critical ? 'down' : undefined} label="Expired or ≤ 7 days" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={data ? counts.warning : undefined} state={counts.warning ? 'warning' : undefined} label="Expiring ≤ 30 days" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={data ? counts.ok : undefined} label="Valid > 30 days" />
        </Card>
        <Card padding="sm">
          <BigNumber size="sm" value={data ? counts.unknown : undefined} label="No data or paused" />
        </Card>
      </div>

      <Card padding="none">
        <QueryState
          query={{ ...query, data: data ? sorted : undefined }}
          errorTitle="Could not load certificates"
          loading={
            <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading certificates">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              icon={ShieldCheck}
              title="No certificates monitored"
              description="Add an HTTPS check to a host, or connect an integration that reports certificates."
              action={<Link href="/hosts" className={buttonClasses({ variant: 'secondary', size: 'sm' })}>Go to hosts</Link>}
            />
          }
        >
          {(rows) => (
            <TableContainer className="relative">
              <Table className="min-w-[720px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Name</Th>
                    <Th>Hostname</Th>
                    <Th>Source</Th>
                    <Th numeric sort={sort} onSort={() => setSort((s) => (s === 'asc' ? 'desc' : 'asc'))}>Expires in</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((c, idx) => {
                    const st = expiryState(c);
                    const isHost = c.source === 'host' && c.id != null;
                    return (
                      <Tr key={isHost ? `host-${c.id}` : `int-${idx}`} selected={selected === c}>
                        <Td><StatusPill status={st.status}>{st.label}</StatusPill></Td>
                        <Td className="max-w-[260px]">
                          {isHost ? (
                            <button
                              type="button"
                              onClick={() => setSelected(c)}
                              className="block max-w-full truncate text-left font-medium text-fg hover:text-accent"
                              aria-label={`Certificate details for ${c.name}`}
                            >
                              {c.name}
                            </button>
                          ) : (
                            <span className="block truncate text-fg">{c.name}</span>
                          )}
                        </Td>
                        <Td className="max-w-[260px] truncate font-mono text-meta text-fg-2">{c.hostname}</Td>
                        <Td>
                          {c.source === 'host' ? (
                            <span className="text-meta text-fg-2">HTTPS host</span>
                          ) : (
                            <div className="flex items-center gap-1.5">
                              <Tag>{c.source_label || c.source || 'Integration'}</Tag>
                              {c.provider && <span className="text-meta text-fg-3">{c.provider}</span>}
                            </div>
                          )}
                        </Td>
                        <Td numeric className={st.status === 'down' ? 'text-down' : st.status === 'warning' ? 'text-warning' : c.days === null ? 'text-fg-3' : undefined}>
                          {daysText(c.days)}
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>

      <SidePanel
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.name ?? 'Certificate'}
        ariaLabel={`Certificate ${selected?.name ?? ''}`}
        icon={<ShieldCheck size={18} className="text-fg-2" aria-hidden="true" />}
        meta={selected ? <span className="font-mono">{selected.hostname}</span> : undefined}
        footer={
          selected?.id != null ? (
            <Link prefetch={false} href={`/hosts/${selected.id}`} className={buttonClasses({ variant: 'secondary' })}>
              Open host
            </Link>
          ) : undefined
        }
      >
        {selected?.id != null && <CertDetail hostId={selected.id} />}
      </SidePanel>
    </div>
  );
}

function CertDetail({ hostId }: { hostId: number }) {
  const query = useQuery<SslDetail>({
    queryKey: ['ssl-detail', hostId],
    queryFn: () => get(`/api/ssl/detail/${hostId}`),
    staleTime: 5 * 60_000,
  });
  const { data, isLoading } = query;

  if (isLoading) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading certificate">
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }

  if (query.isError) {
    return <QueryErrorState compact error={query.error} onRetry={query.refetch} title="Could not load certificate details" />;
  }

  if (!data || !data.ok) {
    return (
      <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">
        Failed to fetch certificate details{data?.error ? `: ${data.error}` : ''}
      </p>
    );
  }

  const st = data.days != null ? expiryState({ days: data.days, enabled: true }) : null;

  return (
    <div className="space-y-5">
      {st && (
        <div className="flex items-center gap-2">
          <StatusPill status={st.status}>{st.label}</StatusPill>
          <span className="text-ui text-fg-2">{data.days != null && data.days >= 0 ? `${data.days} days remaining` : 'Certificate has expired'}</span>
        </div>
      )}

      <PanelSection title={<span className="inline-flex items-center gap-1.5"><Globe size={13} aria-hidden="true" /> Subject</span>}>
        <dl className="space-y-1.5">
          <DetailRow label="Common name" value={data.subject_cn} mono />
          {data.subject_o && <DetailRow label="Organization" value={data.subject_o} />}
          {data.subject && data.subject !== data.subject_cn && <DetailRow label="Full" value={data.subject} small />}
        </dl>
      </PanelSection>

      <PanelSection title={<span className="inline-flex items-center gap-1.5"><Lock size={13} aria-hidden="true" /> Issuer</span>}>
        <dl className="space-y-1.5">
          <DetailRow label="Common name" value={data.issuer_cn} mono />
          {data.issuer_o && <DetailRow label="Organization" value={data.issuer_o} />}
          {data.issuer && data.issuer !== data.issuer_cn && <DetailRow label="Full" value={data.issuer} small />}
        </dl>
      </PanelSection>

      <PanelSection title={<span className="inline-flex items-center gap-1.5"><FileText size={13} aria-hidden="true" /> Validity</span>}>
        <dl className="space-y-1.5">
          <DetailRow label="Not before" value={data.issued_date} />
          <DetailRow
            label="Not after"
            value={data.expiry_date}
            tone={st?.status === 'down' ? 'down' : st?.status === 'warning' ? 'warning' : undefined}
          />
        </dl>
      </PanelSection>

      <PanelSection title={<span className="inline-flex items-center gap-1.5"><Key size={13} aria-hidden="true" /> Technical</span>}>
        <dl className="space-y-1.5">
          {data.signature_algorithm && <DetailRow label="Signature" value={data.signature_algorithm} />}
          {data.key_size && <DetailRow label="Key size" value={`${data.key_size} bit`} />}
          {data.port && <DetailRow label="Port" value={String(data.port)} />}
          {data.serial && <DetailRow label="Serial" value={data.serial} mono small />}
          {data.fingerprint && <DetailRow label="Fingerprint" value={data.fingerprint} mono small />}
        </dl>
      </PanelSection>

      {data.sans && data.sans.length > 0 && (
        <PanelSection title={`Subject alternative names (${data.sans.length})`}>
          <ul className="flex flex-wrap gap-1.5">
            {data.sans.map((san, i) => (
              <li key={i} className="rounded-chip border border-border bg-surface-2 px-2 py-0.5 font-mono text-meta text-fg">
                {san}
              </li>
            ))}
          </ul>
        </PanelSection>
      )}
    </div>
  );
}

function DetailRow({
  label, value, mono, small, tone,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
  small?: boolean;
  tone?: 'down' | 'warning';
}): ReactNode {
  if (!value) return null;
  return (
    <div className="flex items-start gap-3">
      <dt className="w-28 shrink-0 text-meta text-fg-3">{label}</dt>
      <dd
        className={[
          'min-w-0 break-all',
          small ? 'text-meta' : 'text-ui',
          tone === 'down' ? 'text-down' : tone === 'warning' ? 'text-warning' : small ? 'text-fg-2' : 'text-fg',
          mono ? 'font-mono' : '',
        ].join(' ')}
      >
        {value}
      </dd>
    </div>
  );
}
