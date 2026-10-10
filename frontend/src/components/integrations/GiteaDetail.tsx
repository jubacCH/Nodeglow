'use client';

import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { GitBranch, Star, GitFork, AlertCircle, Lock } from 'lucide-react';
import { StatGrid, StatTile, TableCard, formatDate } from './parts';

interface GiteaRepo {
  name: string;
  full_name: string;
  description: string;
  stars: number;
  forks: number;
  open_issues: number;
  updated_at: string;
  private: boolean;
}

interface GiteaData {
  version: string;
  repos_total: number;
  repos_public: number;
  repos_private: number;
  repos: GiteaRepo[];
  users_total: number;
  orgs_total: number;
}

function IconHead({ icon: Icon, label }: { icon: typeof Star; label: string }) {
  return (
    <span className="inline-flex items-center gap-1" title={label}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function GiteaDetail({ data }: { data: GiteaData }) {
  return (
    <div className="space-y-6">
      {/* Stats */}
      <StatGrid cols={5}>
        <StatTile label="Repositories" value={data.repos_total} />
        <StatTile label="Public" value={data.repos_public} />
        <StatTile label="Private" value={data.repos_private} />
        <StatTile label="Users" value={data.users_total} />
        <StatTile label="Organizations" value={data.orgs_total} />
      </StatGrid>

      {/* Version */}
      <Card padding="sm">
        <div className="flex flex-wrap items-center gap-3">
          <GitBranch className="h-4 w-4 text-fg-3" aria-hidden="true" />
          <span className="text-ui text-fg-2">Gitea version</span>
          {data.version ? <Badge className="font-mono">{data.version}</Badge> : <span className="text-fg-3">—</span>}
        </div>
      </Card>

      {/* Repos */}
      {data.repos && data.repos.length > 0 && (
        <TableCard title="Repositories" meta={`${data.repos.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Description</Th>
                <Th numeric><IconHead icon={Star} label="Stars" /></Th>
                <Th numeric><IconHead icon={GitFork} label="Forks" /></Th>
                <Th numeric><IconHead icon={AlertCircle} label="Open issues" /></Th>
                <Th numeric>Updated</Th>
              </Tr>
            </THead>
            <TBody>
              {data.repos.map((r) => (
                <Tr key={r.full_name}>
                  <Td>
                    <span className="flex items-center gap-2 whitespace-nowrap">
                      {r.private && <Lock className="h-3 w-3 shrink-0 text-fg-3" aria-label="Private" />}
                      <span className="font-mono text-meta">{r.full_name}</span>
                    </span>
                  </Td>
                  <Td muted className="max-w-xs truncate text-meta">{r.description || '—'}</Td>
                  <Td numeric muted>{r.stars ?? '—'}</Td>
                  <Td numeric muted>{r.forks ?? '—'}</Td>
                  <Td numeric muted>{r.open_issues ?? '—'}</Td>
                  <Td numeric className="whitespace-nowrap text-meta text-fg-3">{formatDate(r.updated_at, 'date') ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}
    </div>
  );
}
