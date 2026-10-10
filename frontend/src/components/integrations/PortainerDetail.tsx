'use client';

import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { StatGrid, StatTile, StateLabel, TableCard } from './parts';

interface PortainerContainer {
  name: string;
  image: string;
  status: string;
  state: string;
  ports: string;
  created: string;
}

interface PortainerStack {
  name: string;
  type: string;
  status: string;
}

interface PortainerData {
  containers: PortainerContainer[];
  stacks: PortainerStack[];
}

/** Container state. Stopped/exited/paused are usually intentional → dimmed, not red. */
function ContainerState({ state }: { state: string | null | undefined }) {
  switch (state) {
    case 'running': return <StateLabel status="ok">running</StateLabel>;
    case 'stopped':
    case 'exited':
    case 'paused':
    case 'created': return <StateLabel status="disabled">{state}</StateLabel>;
    case 'restarting': return <StateLabel status="warning">restarting</StateLabel>;
    case 'dead': return <StateLabel status="down">dead</StateLabel>;
    default: return <StateLabel status="unknown">{state || 'No data'}</StateLabel>;
  }
}

export function PortainerDetail({ data }: { data: PortainerData }) {
  const { containers, stacks } = data;
  const running = (containers ?? []).filter((c) => c.state === 'running').length;
  const stopped = (containers ?? []).filter((c) => c.state === 'stopped' || c.state === 'exited').length;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <StatGrid>
        <StatTile label="Total containers" value={containers ? containers.length : null} />
        <StatTile label="Running" value={containers ? running : null} />
        <StatTile label="Stopped" value={containers ? stopped : null} />
        <StatTile label="Stacks" value={stacks ? stacks.length : null} />
      </StatGrid>

      {/* Container table */}
      {containers && containers.length > 0 && (
        <TableCard title="Containers" meta={`${containers.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>State</Th>
                <Th>Name</Th>
                <Th>Image</Th>
                <Th>Status</Th>
                <Th>Ports</Th>
              </Tr>
            </THead>
            <TBody>
              {containers.map((c, i) => (
                <Tr key={`${c.name}-${i}`}>
                  <Td className="whitespace-nowrap"><ContainerState state={c.state} /></Td>
                  <Td className="max-w-[220px] truncate font-medium">{c.name}</Td>
                  <Td muted className="max-w-[200px] truncate font-mono text-meta">{c.image}</Td>
                  <Td muted className="whitespace-nowrap text-meta">{c.status || '—'}</Td>
                  <Td muted className="max-w-[240px] truncate font-mono text-meta">{c.ports || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Stacks */}
      {stacks && stacks.length > 0 && (
        <TableCard title="Stacks" meta={`${stacks.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Status</Th>
              </Tr>
            </THead>
            <TBody>
              {stacks.map((s, i) => (
                <Tr key={`${s.name}-${i}`}>
                  <Td>{s.name}</Td>
                  <Td><Badge>{s.type}</Badge></Td>
                  <Td muted>{s.status || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
