'use client';

import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { StatGrid, StatTile, StateLabel, TableCard, formatDate, isNum } from './parts';

interface InfomaniakProduct {
  id: number | null;
  service_name: string;
  customer_name: string;
  internal_name: string;
  /** ISO 8601 UTC, or null when the product does not expire. */
  expires_at: string | null;
  /** null: the API did not say. */
  locked: boolean | null;
  maintenance: boolean | null;
  trial: boolean | null;
}

interface InfomaniakData {
  collected_at?: string;
  product_count: number;
  products: InfomaniakProduct[];
}

/** Days from collection to expiry (negative: expired), or null when unknown. */
function daysLeft(expiresAt: string | null | undefined, collectedAt: string | undefined): number | null {
  if (!expiresAt || !collectedAt) return null;
  const exp = new Date(expiresAt).getTime();
  const now = new Date(collectedAt).getTime();
  if (Number.isNaN(exp) || Number.isNaN(now)) return null;
  return (exp - now) / 86_400_000;
}

/** Mirrors the default alert limits (30 / 7 days). */
function expiryState(days: number | null): HealthState {
  if (days === null) return 'unknown';
  if (days <= 7) return 'down';
  if (days <= 30) return 'warning';
  return 'ok';
}

function expiryText(days: number | null): string {
  if (days === null) return '—';
  if (days <= 0) return 'Expired';
  return `${Math.floor(days)} days`;
}

export function InfomaniakDetail({ data }: { data: InfomaniakData }) {
  if (!data) return null;
  const products = data.products ?? [];
  const withDays = products.map((p) => ({ p, days: daysLeft(p.expires_at, data.collected_at) }));
  // Soonest expiry first; products without a date last.
  const sorted = [...withDays].sort((a, b) => {
    if (a.days === null && b.days === null) return 0;
    if (a.days === null) return 1;
    if (b.days === null) return -1;
    return a.days - b.days;
  });

  const dated = withDays.filter((x) => x.days !== null) as { p: InfomaniakProduct; days: number }[];
  const expired = dated.filter((x) => x.days <= 0).length;
  const soon = dated.filter((x) => x.days > 0 && x.days <= 30).length;
  const locked = products.filter((p) => p.locked === true).length;
  const maintenance = products.filter((p) => p.maintenance === true).length;

  return (
    <div className="space-y-6">
      <StatGrid cols={5}>
        <StatTile label="Products" value={isNum(data.product_count) ? data.product_count : null} />
        <StatTile label="Expired" value={expired} state={expired > 0 ? 'down' : 'ok'} />
        <StatTile label="Expiring within 30 days" value={soon} state={soon > 0 ? 'warning' : 'ok'} />
        <StatTile label="Locked" value={locked} state={locked > 0 ? 'warning' : undefined} />
        <StatTile label="In maintenance" value={maintenance} state={maintenance > 0 ? 'warning' : undefined} />
      </StatGrid>

      <TableCard title="Products" meta={`${products.length}`}>
        <Table>
          <THead>
            <Tr>
              <Th>Name</Th>
              <Th>Type</Th>
              <Th>Expires</Th>
              <Th>Remaining</Th>
              <Th>Flags</Th>
            </Tr>
          </THead>
          <TBody>
            {sorted.map(({ p, days }, i) => (
              <Tr key={p.id ?? i}>
                <Td className="font-medium">{p.customer_name || p.internal_name || (p.id !== null ? `#${p.id}` : '—')}</Td>
                <Td>{p.service_name ? <Badge>{p.service_name}</Badge> : '—'}</Td>
                <Td muted className="whitespace-nowrap">{formatDate(p.expires_at, 'date') ?? '—'}</Td>
                <Td>
                  <StateLabel status={expiryState(days)}>{expiryText(days)}</StateLabel>
                </Td>
                <Td>
                  <span className="flex flex-wrap gap-1">
                    {p.locked === true && <Badge tone="warning">Locked</Badge>}
                    {p.maintenance === true && <Badge tone="maint">Maintenance</Badge>}
                    {p.trial === true && <Badge>Trial</Badge>}
                  </span>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableCard>
    </div>
  );
}
