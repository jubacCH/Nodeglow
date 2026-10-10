'use client';

import { useId, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { cn } from '@/lib/utils';
import {
  Globe, Lock, ArrowRight, Radio, Skull,
  ChevronDown, ChevronRight, type LucideIcon,
} from 'lucide-react';
import { EnabledPill, StatGrid, StatTile, isNum } from './parts';

interface ProxyHost {
  id: number;
  domains: string[];
  domain_primary: string;
  enabled: boolean;
  ssl_forced: boolean;
  certificate_id: number;
  forward: string;
  has_access_list: boolean;
  advanced_config: boolean;
}

interface Certificate {
  id: number;
  nice_name: string;
  provider: string;
  domains: string[];
  expires_on: string | null;
  days_left: number | null;
}

interface Redirection {
  id: number;
  domains: string[];
  forward_url: string;
  forward_scheme: string;
  forward_code: number;
  enabled: boolean;
  preserve_path: boolean;
}

interface Stream {
  id: number;
  incoming_port: number;
  forwarding_host: string;
  forwarding_port: number;
  enabled: boolean;
  tcp: boolean;
  udp: boolean;
}

interface DeadHost {
  id: number;
  domains: string[];
  enabled: boolean;
}

interface NpmData {
  proxy_hosts: ProxyHost[];
  proxy_count: number;
  online_count: number;
  offline_count: number;
  ssl_host_count: number;
  certificates: Certificate[];
  cert_count: number;
  certs_expiring_soon: number;
  certs_expired: number;
  redirections: Redirection[];
  redir_count: number;
  streams: Stream[];
  stream_count: number;
  dead_hosts: DeadHost[];
  dead_count: number;
}

/** Certificate validity: expired or ≤ 7 days down, ≤ 30 days warning. */
function certState(days: number | null | undefined): HealthState {
  if (!isNum(days)) return 'unknown';
  if (days <= 7) return 'down';
  if (days <= 30) return 'warning';
  return 'ok';
}

const CERT_TEXT: Record<HealthState, string> = {
  ok: 'text-ok', degraded: 'text-degraded', warning: 'text-warning', down: 'text-down', maint: 'text-maint', unknown: 'text-fg-3',
};

function Section({ title, icon: Icon, count, children }: {
  title: string; icon: LucideIcon;
  count?: number; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const id = useId();
  return (
    <section>
      <h2 className="mb-3">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 rounded-chip text-body font-medium text-fg transition-colors hover:text-fg-2"
        >
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          <Icon size={14} className="text-fg-3" aria-hidden="true" />
          {title}
          {isNum(count) && <span className="num text-meta font-normal text-fg-3">({count})</span>}
        </button>
      </h2>
      <div id={id} hidden={!open}>{open && children}</div>
    </section>
  );
}

function DomainCell({ domains, primary }: { domains: string[] | undefined; primary?: string }) {
  const list = domains ?? [];
  const first = primary || list[0];
  return (
    <span className="whitespace-nowrap">
      <span className="font-mono">{first || '—'}</span>
      {list.length > 1 && <span className="num ml-1 text-fg-3">+{list.length - 1}</span>}
    </span>
  );
}

export function NpmDetail({ data }: { data: NpmData }) {
  if (!data) return null;
  const expiring = isNum(data.certs_expiring_soon) && isNum(data.certs_expired) ? data.certs_expiring_soon + data.certs_expired : null;
  const certificates = data.certificates ?? [];

  return (
    <div className="space-y-6">
      {/* Overview */}
      <StatGrid cols={5}>
        <StatTile label="Proxy hosts" value={data.proxy_count} />
        <StatTile label="Enabled" value={data.online_count} />
        <StatTile label="Disabled" value={data.offline_count} />
        <StatTile label="SSL certificates" value={data.cert_count} />
        <StatTile
          label="Expiring or expired"
          value={expiring}
          state={isNum(data.certs_expired) && data.certs_expired > 0 ? 'down' : expiring !== null && expiring > 0 ? 'warning' : undefined}
        />
      </StatGrid>

      {/* Proxy hosts */}
      <Section title="Proxy hosts" icon={Globe} count={data.proxy_count}>
        <Card padding="none">
          <TableContainer className="relative">
            <Table>
              <THead>
                <Tr>
                  <Th>Status</Th>
                  <Th>Domain</Th>
                  <Th>Forward to</Th>
                  <Th>SSL</Th>
                </Tr>
              </THead>
              <TBody>
                {(data.proxy_hosts ?? []).map((h) => (
                  <Tr key={h.id}>
                    <Td><EnabledPill enabled={h.enabled} /></Td>
                    <Td><DomainCell domains={h.domains} primary={h.domain_primary} /></Td>
                    <Td muted className="whitespace-nowrap font-mono">{h.forward || '—'}</Td>
                    <Td className="whitespace-nowrap">
                      {h.certificate_id > 0 ? (
                        <span className="flex items-center gap-1 text-fg">
                          <Lock size={11} aria-hidden="true" className="text-fg-3" /> {h.ssl_forced ? 'Forced' : 'On'}
                        </span>
                      ) : (
                        <span className="text-fg-3">None</span>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableContainer>
        </Card>
      </Section>

      {/* SSL certificates */}
      <Section title="SSL certificates" icon={Lock} count={data.cert_count}>
        <div className="space-y-2">
          {certificates.map((cert) => {
            const state = certState(cert.days_left);
            const domains = cert.domains ?? [];
            return (
              <Card key={cert.id} padding="sm">
                <div className="flex min-w-0 flex-wrap items-center gap-3">
                  <StatusDot status={state} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-ui font-medium text-fg">
                      {cert.nice_name || domains[0] || `Cert #${cert.id}`}
                    </p>
                    <p className="truncate font-mono text-meta text-fg-3">{domains.join(', ') || '—'}</p>
                  </div>
                  {cert.provider && <Badge>{cert.provider}</Badge>}
                  <span className={cn('num text-meta', CERT_TEXT[state])}>
                    {isNum(cert.days_left) ? (cert.days_left <= 0 ? 'Expired' : `${cert.days_left}d left`) : '—'}
                  </span>
                </div>
              </Card>
            );
          })}
          {certificates.length === 0 && (
            <p className="py-4 text-center text-ui text-fg-3">No certificates</p>
          )}
        </div>
      </Section>

      {/* Redirections */}
      {data.redir_count > 0 && (
        <Section title="Redirections" icon={ArrowRight} count={data.redir_count}>
          <Card padding="none">
            <TableContainer className="relative">
              <Table>
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Source</Th>
                    <Th>Target</Th>
                    <Th>Code</Th>
                  </Tr>
                </THead>
                <TBody>
                  {(data.redirections ?? []).map((r) => (
                    <Tr key={r.id}>
                      <Td><EnabledPill enabled={r.enabled} /></Td>
                      <Td><DomainCell domains={r.domains} /></Td>
                      <Td muted className="whitespace-nowrap font-mono">{r.forward_scheme}://{r.forward_url}</Td>
                      <Td><Badge>{r.forward_code}</Badge></Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          </Card>
        </Section>
      )}

      {/* Streams */}
      {data.stream_count > 0 && (
        <Section title="Streams" icon={Radio} count={data.stream_count}>
          <Card padding="none">
            <TableContainer className="relative">
              <Table>
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Incoming port</Th>
                    <Th>Forward to</Th>
                    <Th>Protocol</Th>
                  </Tr>
                </THead>
                <TBody>
                  {(data.streams ?? []).map((s) => (
                    <Tr key={s.id}>
                      <Td><EnabledPill enabled={s.enabled} /></Td>
                      <Td className="font-mono">:{s.incoming_port}</Td>
                      <Td muted className="whitespace-nowrap font-mono">{s.forwarding_host}:{s.forwarding_port}</Td>
                      <Td>
                        <span className="flex gap-1">
                          {s.tcp && <Badge>TCP</Badge>}
                          {s.udp && <Badge>UDP</Badge>}
                        </span>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          </Card>
        </Section>
      )}

      {/* 404 hosts */}
      {data.dead_count > 0 && (
        <Section title="404 hosts" icon={Skull} count={data.dead_count}>
          <div className="space-y-2">
            {(data.dead_hosts ?? []).map((d) => (
              <Card key={d.id} padding="sm">
                <div className="flex min-w-0 items-center gap-3">
                  <Skull size={14} className="shrink-0 text-fg-3" aria-hidden="true" />
                  <span className="min-w-0 flex-1 break-all font-mono text-ui text-fg">{(d.domains ?? []).join(', ') || '—'}</span>
                  <EnabledPill enabled={d.enabled} />
                </div>
              </Card>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
