'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, Pencil, Plus, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { RuleEditor } from '@/components/rules/RuleEditor';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Switch } from '@/components/ui/Field';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, get, post } from '@/lib/api';
import { formatAgo, formatDateTime } from '@/lib/incidents';
import { conditionLabel } from '@/lib/rules';
import { useIsEditor } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import type { AlertRule } from '@/types';

const RULES_KEY = ['rules'];

function LastTriggered({ at }: { at: string | null }) {
  if (!at) return <span className="text-fg-3">Never</span>;
  return <time dateTime={at} title={formatDateTime(at)}>{formatAgo(at)}</time>;
}

function SeverityBadge({ severity }: { severity: AlertRule['severity'] }) {
  return <Badge variant="severity" severity={severity}>{severity.charAt(0).toUpperCase() + severity.slice(1)}</Badge>;
}

export default function RulesPage() {
  useEffect(() => { document.title = 'Alert rules | Nodeglow'; }, []);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const canEdit = useIsEditor();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AlertRule | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const query = useQuery({
    queryKey: RULES_KEY,
    queryFn: () => get<AlertRule[]>('/api/v1/rules'),
  });

  const openAdd = () => { setEditing(null); setEditorOpen(true); };
  const openEdit = (rule: AlertRule) => { setEditing(rule); setEditorOpen(true); };

  async function toggleRule(rule: AlertRule) {
    setBusyId(rule.id);
    const flip = (enabled: boolean) =>
      qc.setQueryData<AlertRule[]>(RULES_KEY, (list) => list?.map((r) => (r.id === rule.id ? { ...r, enabled } : r)));
    flip(!rule.enabled);
    try {
      await post(`/api/v1/rules/${rule.id}/toggle`);
    } catch (e) {
      flip(rule.enabled);
      toast(apiErrorMessage(e, `Could not ${rule.enabled ? 'disable' : 'enable'} the rule`), 'error');
    } finally {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: RULES_KEY });
    }
  }

  async function deleteRule(rule: AlertRule) {
    const ok = await confirm({
      title: 'Delete alert rule',
      description: `Delete "${rule.name}"? It stops alerting immediately. Existing incidents stay.`,
      confirmLabel: 'Delete rule',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await post(`/api/v1/rules/${rule.id}/delete`);
      toast(`Deleted "${rule.name}"`, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not delete the rule'), 'error');
    } finally {
      qc.invalidateQueries({ queryKey: RULES_KEY });
    }
  }

  const rules = query.data;
  const enabledCount = rules?.filter((r) => r.enabled).length ?? 0;
  const asOf = formatAsOf(query.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="Alert rules"
        description={rules
          ? `${rules.length} rule${rules.length === 1 ? '' : 's'}, ${enabledCount} enabled${asOf ? ` · updated ${asOf}` : ''}`
          : 'Thresholds on integration data that open incidents'}
        actions={canEdit && (
          <Button onClick={openAdd}><Plus size={15} aria-hidden="true" /> New rule</Button>
        )}
      />

      <QueryState
        query={query}
        errorTitle="Could not load alert rules"
        loading={
          <Card padding="none" aria-busy="true" aria-label="Loading rules">
            <div className="divide-y divide-border">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-3">
                  <Skeleton className="h-4 w-8" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-4 w-20" />
                </div>
              ))}
            </div>
          </Card>
        }
        empty={
          <Card>
            <EmptyState
              icon={Bell}
              title="No alert rules yet"
              description="Rules evaluate integration fields on every poll and open incidents when a threshold is crossed."
              action={canEdit ? <Button size="sm" onClick={openAdd}><Plus size={14} aria-hidden="true" /> Create the first rule</Button> : undefined}
            />
          </Card>
        }
      >
        {(list) => (
          <>
            <Card padding="none" className="overflow-hidden max-[759px]:hidden">
              <TableContainer>
                <Table aria-label="Alert rules">
                  <THead>
                    <Tr>
                      <Th className="w-[96px]">Enabled</Th>
                      <Th>Rule</Th>
                      <Th className="w-[110px]">Severity</Th>
                      <Th className="w-[150px]">Source</Th>
                      <Th numeric className="w-[140px]">Last triggered</Th>
                      {canEdit && <Th className="w-[96px]"><span className="sr-only">Actions</span></Th>}
                    </Tr>
                  </THead>
                  <TBody>
                    {list.map((rule) => (
                      <Tr key={rule.id} className="hover:bg-hover">
                        <Td>
                          {canEdit ? (
                            <Switch checked={rule.enabled} disabled={busyId === rule.id} onChange={() => toggleRule(rule)} aria-label={`Enable ${rule.name}`} />
                          ) : (
                            <span className={rule.enabled ? 'text-fg' : 'text-fg-3'}>{rule.enabled ? 'On' : 'Off'}</span>
                          )}
                        </Td>
                        <Td className="max-w-0 py-2">
                          <p className={rule.enabled ? 'truncate font-medium text-fg' : 'truncate font-medium text-fg-2'}>{rule.name}</p>
                          <p className="truncate font-mono text-meta text-fg-3" title={conditionLabel(rule)}>{conditionLabel(rule)}</p>
                        </Td>
                        <Td><SeverityBadge severity={rule.severity} /></Td>
                        <Td muted className="truncate">{rule.source_type}{rule.source_id != null && <span className="text-fg-3"> #{rule.source_id}</span>}</Td>
                        <Td numeric><LastTriggered at={rule.last_triggered_at} /></Td>
                        {canEdit && (
                          <Td>
                            <div className="flex justify-end gap-0.5">
                              <IconButton size="sm" aria-label={`Edit ${rule.name}`} onClick={() => openEdit(rule)}><Pencil size={14} aria-hidden="true" /></IconButton>
                              <IconButton size="sm" aria-label={`Delete ${rule.name}`} onClick={() => deleteRule(rule)} className="hover:text-down"><Trash2 size={14} aria-hidden="true" /></IconButton>
                            </div>
                          </Td>
                        )}
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </TableContainer>
            </Card>

            <ul className="space-y-2 min-[760px]:hidden" aria-label="Alert rules">
              {list.map((rule) => (
                <li key={rule.id}>
                  <Card padding="sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className={rule.enabled ? 'font-medium text-fg [overflow-wrap:anywhere]' : 'font-medium text-fg-2 [overflow-wrap:anywhere]'}>{rule.name}</p>
                        <p className="font-mono text-meta text-fg-3 [overflow-wrap:anywhere]">{conditionLabel(rule)}</p>
                      </div>
                      <SeverityBadge severity={rule.severity} />
                    </div>
                    <p className="mt-1.5 text-meta text-fg-2">
                      {rule.source_type} · last triggered <LastTriggered at={rule.last_triggered_at} />
                    </p>
                    {canEdit && (
                      <div className="mt-2 flex items-center gap-1 border-t border-border pt-2">
                        <Switch checked={rule.enabled} disabled={busyId === rule.id} onChange={() => toggleRule(rule)} label="Enabled" className="mr-auto" />
                        <IconButton size="sm" aria-label={`Edit ${rule.name}`} onClick={() => openEdit(rule)}><Pencil size={14} aria-hidden="true" /></IconButton>
                        <IconButton size="sm" aria-label={`Delete ${rule.name}`} onClick={() => deleteRule(rule)}><Trash2 size={14} aria-hidden="true" /></IconButton>
                      </div>
                    )}
                  </Card>
                </li>
              ))}
            </ul>
          </>
        )}
      </QueryState>

      <RuleEditor
        open={editorOpen}
        rule={editing}
        onClose={() => setEditorOpen(false)}
        onSaved={(created) => {
          toast(created ? 'Rule created' : 'Rule updated', 'success');
          qc.invalidateQueries({ queryKey: RULES_KEY });
        }}
      />
      {ConfirmDialogElement}
    </div>
  );
}
