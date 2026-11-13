'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { SectionTitle, StatGrid, StatTile, TableCard, UsageBar, formatDate, isNum } from './parts';

interface M365Service {
  id: string;
  service: string;
  status: string | null;
  state: 'ok' | 'critical' | 'warning' | 'recovered';
}

interface M365Issue {
  id: string;
  title: string;
  service: string;
  status: string | null;
  state: 'ok' | 'critical' | 'warning' | 'recovered';
  classification: string | null;
  impact: string | null;
  start: string | null;
  last_modified: string | null;
}

interface M365Sku {
  sku_id: string;
  sku: string;
  capability_status: string | null;
  consumed: number | null;
  enabled: number | null;
  suspended: number | null;
  warning: number | null;
  locked_out: number | null;
  pct: number | null;
}

interface M365Credential {
  app: string;
  app_id: string;
  object_id: string;
  kind: 'secret' | 'certificate';
  name: string | null;
  key_id: string;
  start: string | null;
  end: string | null;
  days_left: number | null;
}

interface M365Totals {
  services_total: number | null;
  services_unhealthy: number | null;
  open_issues: number | null;
  skus_total: number | null;
  apps_total: number | null;
  credentials_total: number | null;
  credentials_expired: number | null;
}

interface M365Data {
  collected_at?: string;
  services: M365Service[] | null;
  issues: M365Issue[] | null;
  skus: M365Sku[] | null;
  credentials: M365Credential[] | null;
  totals?: M365Totals;
  unavailable?: string[];
}

function serviceState(s: M365Service['state'] | undefined): HealthState {
  if (s === 'ok') return 'ok';
  if (s === 'critical') return 'down';
  if (s === 'warning' || s === 'recovered') return 'warning';
  return 'unknown';
}

function credentialState(days: number | null | undefined): HealthState {
  if (!isNum(days)) return 'unknown';
  if (days < 0 || days <= 7) return 'down';
  if (days <= 30) return 'warning';
  return 'ok';
}

function credentialText(days: number | null | undefined): string {
  if (!isNum(days)) return '—';
  if (days < 0) return `Expired ${-days} d ago`;
  return `${days} d`;
}

export function M365Detail({ data }: { data: M365Data }) {
  const { services, issues, skus, credentials } = data;
  const totals = data.totals ?? ({} as Partial<M365Totals>);
  const unavailable = data.unavailable ?? [];
  const unhealthy = isNum(totals.services_unhealthy) ? totals.services_unhealthy : null;

  return (
    <div className="space-y-6">
      {unavailable.length > 0 && (
        <Card as="section">
          <CardHeader title="Not available" meta={`${unavailable.length}`} />
          <ul className="space-y-1 text-ui text-fg-2">
            {unavailable.map((u) => (
              <li key={u} className="wrap-break-word">{u}</li>
            ))}
          </ul>
        </Card>
      )}

      <StatGrid>
        <StatTile
          label="Services affected"
          value={unhealthy}
          state={unhealthy === null ? undefined : unhealthy > 0 ? 'warning' : 'ok'}
        />
        <StatTile label="Open issues" value={totals.open_issues} state={isNum(totals.open_issues) && totals.open_issues > 0 ? 'warning' : undefined} />
        <StatTile label="App registrations" value={totals.apps_total} />
        <StatTile
          label="Expired credentials"
          value={totals.credentials_expired}
          state={isNum(totals.credentials_expired) && totals.credentials_expired > 0 ? 'warning' : undefined}
        />
      </StatGrid>

      {/* Service health */}
      {services && services.length > 0 && (
        <section>
          <SectionTitle meta={`${services.length}`}>Service health</SectionTitle>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {services.map((s) => (
              <Card key={s.id || s.service} padding="sm">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-ui font-medium text-fg">{s.service || '—'}</span>
                  <StatusPill status={serviceState(s.state)}>{s.status || undefined}</StatusPill>
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Open issues */}
      {issues && issues.length > 0 && (
        <Card as="section">
          <CardHeader title="Open issues" meta={`${issues.length}`} />
          <ul className="space-y-3">
            {issues.map((i) => (
              <li key={i.id} className="flex min-w-0 items-start gap-3 text-ui">
                <Badge tone={i.state === 'critical' ? 'down' : i.classification === 'incident' ? 'warning' : 'neutral'} className="shrink-0">
                  {i.classification || '—'}
                </Badge>
                <div className="min-w-0 flex-1">
                  <p className="wrap-break-word text-fg">{i.title || i.id}</p>
                  <p className="mt-0.5 text-meta text-fg-3">
                    {[i.service, i.id, i.status, formatDate(i.start)].filter(Boolean).join(' · ')}
                  </p>
                  {i.impact && <p className="mt-0.5 wrap-break-word text-meta text-fg-2">{i.impact}</p>}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {issues && issues.length === 0 && (
        <Card as="section">
          <CardHeader title="Open issues" meta="0" />
          <p className="text-ui text-fg-3">No open service issues reported.</p>
        </Card>
      )}

      {/* Licences */}
      {skus && skus.length > 0 && (
        <section>
          <SectionTitle meta={`${skus.length}`}>Licences</SectionTitle>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {skus.map((s) => (
              <Card key={s.sku_id || s.sku} padding="sm" className="space-y-3">
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate font-mono text-ui font-medium text-fg">{s.sku || '—'}</span>
                  {s.capability_status && s.capability_status !== 'Enabled' && (
                    <Badge tone="warning">{s.capability_status}</Badge>
                  )}
                </div>
                <UsageBar
                  label="Assigned"
                  pct={s.pct}
                  detail={isNum(s.consumed) && isNum(s.enabled) && isNum(s.pct)
                    ? `${s.consumed} / ${s.enabled} (${s.pct.toFixed(1)} %)`
                    : undefined}
                />
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* App registration credentials */}
      {credentials && credentials.length > 0 && (
        <TableCard title="App credentials" meta={`${credentials.length}`} maxHeight={480}>
          <Table>
            <THead>
              <Tr>
                <Th>App</Th>
                <Th>Type</Th>
                <Th>Name</Th>
                <Th>Expires</Th>
                <Th numeric>Remaining</Th>
              </Tr>
            </THead>
            <TBody>
              {credentials.map((c) => (
                <Tr key={`${c.object_id}-${c.kind}-${c.key_id}-${c.end ?? ''}`}>
                  <Td className="whitespace-nowrap">{c.app || '—'}</Td>
                  <Td><Badge>{c.kind === 'secret' ? 'Secret' : 'Certificate'}</Badge></Td>
                  <Td muted className="whitespace-nowrap">{c.name || '—'}</Td>
                  <Td muted className="whitespace-nowrap">{formatDate(c.end, 'date') ?? '—'}</Td>
                  <Td numeric className="whitespace-nowrap">
                    <StatusPill status={credentialState(c.days_left)} size="sm">{credentialText(c.days_left)}</StatusPill>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
