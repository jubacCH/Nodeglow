'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Pill } from '@/components/ui/Tag';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { KV, KVGrid, StatGrid, StatTile, StateLabel, TableCard, formatDate } from './parts';

interface HassAutomation {
  entity_id: string;
  name: string;
  state: string;
  last_triggered: string | null;
}

interface HassPerson {
  name: string;
  state: string;
}

interface HassData {
  version: string;
  location_name: string;
  timezone: string;
  components: number;
  entities: {
    total: number;
    by_domain: Record<string, number>;
  };
  automations: HassAutomation[];
  persons: HassPerson[];
}

/** Automation state: off is intentional (dimmed), unavailable/unknown is no data. */
function AutomationState({ state }: { state: string | null | undefined }) {
  if (state === 'on') return <StateLabel status="ok">on</StateLabel>;
  if (state === 'off') return <StateLabel status="disabled">off</StateLabel>;
  return <StateLabel status="unknown">{state || 'No data'}</StateLabel>;
}

export function HassDetail({ data }: { data: HassData }) {
  const domains = Object.entries(data.entities?.by_domain ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      {/* Stats */}
      <StatGrid>
        <StatTile label="Total entities" value={data.entities?.total} />
        <StatTile label="Components" value={data.components} />
        <StatTile label="Automations" value={data.automations?.length} />
        <StatTile label="Persons" value={data.persons?.length} />
      </StatGrid>

      {/* System info */}
      <Card as="section">
        <CardHeader title="Instance" />
        <KVGrid>
          <KV label="Location">{data.location_name}</KV>
          <KV label="Version" mono>{data.version}</KV>
          <KV label="Timezone">{data.timezone}</KV>
          <KV label="Components">{data.components}</KV>
        </KVGrid>
      </Card>

      {/* Entity domains */}
      {domains.length > 0 && (
        <Card as="section">
          <CardHeader title="Entities by domain" meta={`${domains.length}`} />
          <ul className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 md:grid-cols-4 lg:grid-cols-5">
            {domains.map(([domain, count]) => (
              <li key={domain} className="flex min-w-0 items-center justify-between gap-2 rounded-ctl bg-surface-2 px-3 py-2">
                <span className="truncate font-mono text-meta text-fg-2">{domain}</span>
                <span className="num shrink-0 text-meta text-fg-3">{count}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Automations */}
      {data.automations && data.automations.length > 0 && (
        <TableCard title="Automations" meta={`${data.automations.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>State</Th>
                <Th>Name</Th>
                <Th numeric>Last triggered</Th>
              </Tr>
            </THead>
            <TBody>
              {data.automations.map((a) => (
                <Tr key={a.entity_id}>
                  <Td className="whitespace-nowrap"><AutomationState state={a.state} /></Td>
                  <Td className="max-w-[320px] truncate">{a.name}</Td>
                  <Td numeric muted className="whitespace-nowrap text-meta">{formatDate(a.last_triggered) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Persons */}
      {data.persons && data.persons.length > 0 && (
        <Card as="section">
          <CardHeader title="Persons" />
          <div className="flex flex-wrap gap-2">
            {data.persons.map((p) => (
              <Pill key={p.name} tone={p.state === 'home' ? 'accent' : 'neutral'} dot>
                <span className="text-fg">{p.name}</span>
                <span className="text-fg-3">{p.state || '—'}</span>
              </Pill>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
