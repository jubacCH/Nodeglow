'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, StatGrid, StatTile, StateLabel, TableCard, formatDate, isNum } from './parts';

type DomainKind = 'ok' | 'no_expiry' | 'no_rdap' | 'not_found' | 'error';

interface DomainRecord {
  name: string;
  tld?: string;
  kind: DomainKind;
  /** ISO 8601 UTC, null when the registry does not publish an expiry. */
  expiry: string | null;
  registrar?: string;
  status?: string[];
  rdap_server?: string;
  reason?: string;
}

interface DomainData {
  collected_at?: string;
  domains?: DomainRecord[];
  unavailable?: string[];
  invalid_domains?: string[];
  totals?: {
    domains?: number;
    with_expiry?: number;
    unavailable?: number;
    not_found?: number;
  };
}

// Display-only defaults; alerting uses the tenant's thresholds (30 / 7 days).
const WARN_DAYS = 30;
const CRIT_DAYS = 7;
const DAY_MS = 86_400_000;

function isHold(status: string): boolean {
  const flat = status.toLowerCase().replace(/[^a-z]/g, '');
  return flat === 'clienthold' || flat === 'serverhold';
}

/** Whole days until expiry relative to the snapshot time, or null without both dates. */
function daysLeft(expiry: string | null | undefined, collectedAt: string | undefined): number | null {
  if (!expiry || !collectedAt) return null;
  const e = new Date(expiry).getTime();
  const c = new Date(collectedAt).getTime();
  if (Number.isNaN(e) || Number.isNaN(c)) return null;
  return Math.floor((e - c) / DAY_MS);
}

function rowState(rec: DomainRecord, days: number | null): HealthState {
  if (rec.kind === 'not_found') return 'down';
  if ((rec.status ?? []).some(isHold)) return 'down';
  if (rec.kind !== 'ok' || days === null) return 'unknown';
  if (days <= CRIT_DAYS) return 'down';
  if (days <= WARN_DAYS) return 'warning';
  return 'ok';
}

function rowLabel(rec: DomainRecord, days: number | null): string {
  if (rec.kind === 'not_found') return 'Not found';
  if ((rec.status ?? []).some(isHold)) return 'On hold';
  if (rec.kind !== 'ok' || days === null) return 'No expiry data';
  if (days < 0) return 'Expired';
  if (days === 0) return 'Expires today';
  return days === 1 ? '1 day left' : `${days} days left`;
}

export function DomainDetail({ data }: { data: DomainData }) {
  const domains = data.domains ?? [];
  const unavailable = data.unavailable ?? [];
  const invalid = data.invalid_domains ?? [];
  const totals = data.totals ?? {};

  const nextExpiry = domains
    .filter((d) => d.kind === 'ok' && d.expiry)
    .map((d) => daysLeft(d.expiry, data.collected_at))
    .filter((d): d is number => d !== null)
    .sort((a, b) => a - b)[0];

  return (
    <div className="space-y-6">
      <StatGrid>
        <StatTile label="Domains" value={totals.domains} />
        <StatTile label="With expiry date" value={totals.with_expiry} />
        <StatTile
          label="Next expiry"
          value={isNum(nextExpiry) ? nextExpiry : null}
          unit={isNum(nextExpiry) ? 'days' : undefined}
          state={isNum(nextExpiry) ? (nextExpiry <= CRIT_DAYS ? 'down' : nextExpiry <= WARN_DAYS ? 'warning' : undefined) : undefined}
        />
        <StatTile
          label="No expiry data"
          value={totals.unavailable}
          state={isNum(totals.unavailable) && totals.unavailable > 0 ? 'unknown' : undefined}
        />
      </StatGrid>

      {domains.length > 0 && (
        <TableCard title="Domains" meta={`${domains.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Domain</Th>
                <Th>Status</Th>
                <Th>Expires</Th>
                <Th>Registrar</Th>
                <Th>Registry status</Th>
              </Tr>
            </THead>
            <TBody>
              {domains.map((d) => {
                const days = daysLeft(d.expiry, data.collected_at);
                return (
                  <Tr key={d.name}>
                    <Td className="font-mono">{d.name}</Td>
                    <Td>
                      <StateLabel status={rowState(d, days)}>{rowLabel(d, days)}</StateLabel>
                      {d.kind !== 'ok' && d.reason && <p className="mt-0.5 text-meta text-fg-3">{d.reason}</p>}
                    </Td>
                    <Td muted className="whitespace-nowrap">{formatDate(d.expiry, 'date') ?? '—'}</Td>
                    <Td muted>{d.registrar || '—'}</Td>
                    <Td muted className="text-meta">{d.status && d.status.length > 0 ? d.status.join(', ') : '—'}</Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableCard>
      )}

      {unavailable.length > 0 && (
        <Card as="section">
          <CardHeader title="Could not ask" meta={`${unavailable.length}`} />
          <p className="mb-2 text-ui text-fg-2">
            These domains were not checked: the registry has no RDAP service or publishes no expiry date. They are
            not healthy, only unknown.
          </p>
          <p className="wrap-break-word font-mono text-ui text-fg">{unavailable.join(', ')}</p>
        </Card>
      )}

      {invalid.length > 0 && (
        <Card as="section">
          <CardHeader title="Invalid entries" meta={`${invalid.length}`} />
          <p className="wrap-break-word font-mono text-ui text-warning">{invalid.join(', ')}</p>
        </Card>
      )}

      <Card as="section">
        <CardHeader title="Collection" />
        <KVGrid>
          <KV label="Collected">{formatDate(data.collected_at)}</KV>
          <KV label="Source">RDAP (IANA bootstrap)</KV>
        </KVGrid>
      </Card>
    </div>
  );
}
