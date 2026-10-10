'use client';

import { useParams } from 'next/navigation';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Pencil, Plus, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { Card } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import { Badge } from '@/components/ui/Badge';
import { Tag } from '@/components/ui/Tag';
import { Button, IconButton } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { SidePanel } from '@/components/ui/SidePanel';
import { Checkbox, Field, Input, Select } from '@/components/ui/Field';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import { get, post, del, api, apiErrorBody, apiErrorMessage } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { useIsAdmin } from '@/stores/auth';
import { integrationHealth } from '../_components/health';

interface ConfigField {
  key: string;
  label: string;
  field_type: string;
  placeholder: string;
  required: boolean;
  default: string | number | boolean;
  options: { value: string; label: string }[] | null;
}

interface FieldsResponse {
  type: string;
  display_name: string;
  description: string;
  fields: ConfigField[];
}

/** Fields that point the integration at a server. */
function isEndpointField(f: ConfigField): boolean {
  return f.field_type === 'url' || /^(host|hostname|url|base_url|api_url|endpoint|server|address)$/.test(f.key);
}

function isSecretField(f: ConfigField): boolean {
  return f.field_type === 'password';
}

function titleCase(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export default function IntegrationListPage() {
  const params = useParams();
  const type = params.type as string;
  const qc = useQueryClient();
  const instancesQuery = useIntegrations(type);
  const { data: integrations } = instancesQuery;
  const [showAdd, setShowAdd] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [formData, setFormData] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  // Creating, editing and deleting integrations is admin-only on the backend.
  const isAdmin = useIsAdmin();
  const [formError, setFormError] = useState('');
  const [missingFields, setMissingFields] = useState<string[]>([]);

  const { data: fieldsData, isLoading: fieldsLoading } = useQuery({
    queryKey: ['integration-fields', type],
    queryFn: () => get<FieldsResponse>(`/api/integration/${type}/fields`),
    enabled: showAdd || editId !== null,
  });

  const displayName = fieldsData?.display_name ?? titleCase(type);
  useEffect(() => { document.title = `${displayName} | Nodeglow`; }, [displayName]);

  function clearFormErrors() {
    setFormError('');
    setMissingFields([]);
  }

  /** Show the backend's reason; for secrets_required, flag the fields. */
  function showSaveError(e: unknown, fallback: string) {
    const body = apiErrorBody(e);
    setFormError(apiErrorMessage(e, fallback));
    setMissingFields(body?.code === 'secrets_required' && Array.isArray(body.missing_fields) ? body.missing_fields : []);
  }

  // Edit form: endpoint fields start empty, so any value typed there is a
  // change of address — and the backend then wants the secrets re-entered
  // rather than sending stored credentials to a new server.
  const endpointChanged =
    editId !== null &&
    (fieldsData?.fields ?? []).some((f) => isEndpointField(f) && String(formData[f.key] ?? '').trim() !== '');

  function secretRequired(f: ConfigField): boolean {
    return isSecretField(f) && (missingFields.includes(f.key) || (endpointChanged && !String(formData[f.key] ?? '')));
  }

  function resetForm() {
    clearFormErrors();
    const defaults: Record<string, string | boolean> = {};
    if (fieldsData?.fields) {
      for (const f of fieldsData.fields) {
        defaults[f.key] = f.field_type === 'checkbox' ? !!f.default : String(f.default ?? '');
      }
    }
    setFormData(defaults);
  }

  async function handleAdd() {
    setSaving(true);
    clearFormErrors();
    try {
      await post(`/api/integration/${type}/create`, {
        name: formData.name || '',
        ...formData,
      });
      qc.invalidateQueries({ queryKey: ['integrations', type] });
      setShowAdd(false);
      setFormData({});
    } catch (e) {
      showSaveError(e, 'Failed to create integration');
    } finally {
      setSaving(false);
    }
  }

  function openEdit(id: number, name: string, clusterGroup: string | null) {
    clearFormErrors();
    setShowAdd(false);
    setEditId(id);
    // Pre-fill name + cluster_group; secret config fields stay empty (password-safe)
    setFormData({ name, cluster_group: clusterGroup ?? '' });
  }

  function closeEdit() {
    setEditId(null);
    setFormData({});
  }

  async function handleSaveEdit() {
    if (editId === null) return;
    setSaving(true);
    clearFormErrors();
    try {
      await api(`/api/integration/${type}/${editId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: formData.name || '', ...formData }),
      });
      qc.invalidateQueries({ queryKey: ['integrations', type] });
      setEditId(null);
      setFormData({});
      toast('Integration updated', 'success');
    } catch (e) {
      showSaveError(e, 'Failed to update integration');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number, name: string) {
    const ok = await confirm({ title: 'Delete integration', description: `Delete the instance "${name}"? This cannot be undone.`, confirmLabel: 'Delete', variant: 'danger' });
    if (!ok) return;
    try {
      await del(`/api/integration/${type}/${id}`);
      qc.invalidateQueries({ queryKey: ['integrations', type] });
    } catch (err) {
      toast(apiErrorMessage(err, 'Failed to delete integration'), 'error');
    }
  }

  function openAdd() {
    setEditId(null);
    setShowAdd(true);
    resetForm();
  }

  // Defaults arrive after the panel opened: apply them once.
  useEffect(() => {
    if (showAdd && fieldsData && Object.keys(formData).length === 0) resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAdd, fieldsData]);

  const editing = editId !== null;
  const panelOpen = showAdd || editing;
  const editName = editing ? integrations?.find((i) => i.id === editId)?.name : undefined;

  const list = integrations ?? [];
  const now = Date.now();
  const failing = list.filter((i) => integrationHealth(i, i.last_check, now).status === 'down').length;
  const asOf = formatAsOf(instancesQuery.dataUpdatedAt);

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Integrations', href: '/integration/store' }, { label: displayName }]} />
      <PageHeader
        title={displayName}
        description={
          integrations
            ? `${list.length} instance${list.length === 1 ? '' : 's'}${failing ? ` · ${failing} failing` : ''}${asOf ? ` · updated ${asOf}` : ''}`
            : fieldsData?.description ?? `Integration instances for ${type}`
        }
        actions={
          isAdmin ? (
            <Button onClick={openAdd}>
              <Plus size={16} aria-hidden="true" />
              Add instance
            </Button>
          ) : undefined
        }
      />

      <Card padding="none">
        <QueryState
          query={instancesQuery}
          errorTitle="Could not load instances"
          loading={
            <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading instances">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              icon={Plus}
              title={`No ${displayName} instances`}
              description={
                fieldsData?.description
                  ? `Add an instance to start collecting data. ${fieldsData.description}`
                  : 'Add an instance to start monitoring.'
              }
              action={
                isAdmin ? (
                  <Button size="sm" onClick={openAdd}>
                    <Plus size={14} aria-hidden="true" /> Add instance
                  </Button>
                ) : undefined
              }
            />
          }
        >
          {(rows) => (
            <TableContainer className="relative">
              <Table className="min-w-[760px]">
                <THead>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Instance</Th>
                    <Th>Last collection</Th>
                    <Th>Details</Th>
                    {isAdmin && <Th><span className="sr-only">Actions</span></Th>}
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((int) => {
                    const h = integrationHealth(int, int.last_check, now);
                    return (
                      <Tr key={int.id}>
                        <Td className="align-top pt-2.5">
                          <StatusPill status={h.status}>{h.label}</StatusPill>
                        </Td>
                        <Td className="max-w-[280px] py-2 align-top">
                          <Link href={`/integration/${type}/${int.id}`} className="block truncate font-medium text-fg hover:text-accent">
                            {int.name}
                          </Link>
                          <div className="mt-1 flex flex-wrap items-center gap-1">
                            {int.is_standby && <Badge tone="maint">Standby</Badge>}
                            {int.cluster_group && (
                              <Tag title="Cluster group — instances sharing this name run as one HA source">
                                Cluster · <span className="ml-1 font-mono">{int.cluster_group}</span>
                              </Tag>
                            )}
                            {int.created_at && <span className="text-micro text-fg-3">Created {new Date(int.created_at).toLocaleDateString()}</span>}
                          </div>
                        </Td>
                        <Td className="whitespace-nowrap py-2 align-top">
                          {int.last_check ? (
                            <>
                              <time
                                dateTime={int.last_check}
                                title={new Date(int.last_check).toLocaleString()}
                                className={h.lastOk ? 'text-fg' : 'text-down'}
                              >
                                {h.lastOk ? 'Succeeded' : 'Failed'} {timeAgo(int.last_check)}
                              </time>
                              <span className="block text-micro text-fg-3">{h.reason}</span>
                            </>
                          ) : (
                            <span className="text-fg-3">Never</span>
                          )}
                        </Td>
                        <Td className="max-w-[420px] py-2 align-top">
                          {int.error && int.enabled ? (
                            <p className="line-clamp-2 break-words font-mono text-meta text-down" title={int.error}>{int.error}</p>
                          ) : (
                            <span className="text-meta text-fg-3">{h.status === 'unknown' ? h.reason : '—'}</span>
                          )}
                        </Td>
                        {isAdmin && (
                          <Td className="whitespace-nowrap py-2 text-right align-top">
                            <IconButton size="sm" aria-label={`Edit ${int.name}`} title="Edit" onClick={() => openEdit(int.id, int.name, int.cluster_group)}>
                              <Pencil size={14} aria-hidden="true" />
                            </IconButton>
                            <IconButton size="sm" aria-label={`Delete ${int.name}`} title="Delete" onClick={() => handleDelete(int.id, int.name)}>
                              <Trash2 size={14} aria-hidden="true" />
                            </IconButton>
                          </Td>
                        )}
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
        open={panelOpen}
        onClose={() => (editing ? closeEdit() : setShowAdd(false))}
        width="lg"
        title={editing ? `Edit ${editName ?? 'instance'}` : `New ${displayName} instance`}
        meta={editing ? 'Secret fields stay empty: leave them blank to keep the stored value.' : fieldsData?.description}
        footer={
          <>
            <Button onClick={editing ? handleSaveEdit : handleAdd} loading={saving}>
              {saving ? 'Saving…' : editing ? 'Save changes' : 'Create'}
            </Button>
            <Button variant="secondary" onClick={() => (editing ? closeEdit() : setShowAdd(false))}>Cancel</Button>
          </>
        }
      >
        {formError && (
          <p role="alert" className="mb-4 rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{formError}</p>
        )}
        {editing && endpointChanged && (fieldsData?.fields ?? []).some(isSecretField) && (
          <p role="status" className="mb-4 rounded-ctl border border-warning/30 bg-warning-soft px-3 py-2 text-meta text-warning">
            You are changing the address — re-enter the secret fields too. Stored credentials are not sent to a new server.
          </p>
        )}
        <form
          className="grid grid-cols-1 gap-4 sm:grid-cols-2"
          onSubmit={(e) => { e.preventDefault(); if (editing) handleSaveEdit(); else handleAdd(); }}
        >
          <Field label="Name">
            <Input
              type="text"
              placeholder={editing ? undefined : `My ${displayName}`}
              value={(formData.name as string) ?? ''}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </Field>

          {/* Cluster group — HA grouping for instances of the same logical
              source. Auto-populated for Proxmox from cluster_name. */}
          <Field label="Cluster group" hint="Optional, for HA. Leave empty if standalone.">
            <Input
              type="text"
              placeholder="e.g. cluster-a"
              value={(formData.cluster_group as string) ?? ''}
              onChange={(e) => setFormData({ ...formData, cluster_group: e.target.value })}
            />
          </Field>

          {fieldsLoading && (
            <div className="space-y-3 sm:col-span-2" aria-busy="true" aria-label="Loading fields">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          )}

          {fieldsData?.fields.map((field) => {
            const required = field.required || secretRequired(field);
            if (field.field_type === 'checkbox') {
              return (
                <div key={field.key} className="flex items-end pb-2">
                  <Checkbox
                    label={field.label}
                    checked={!!formData[field.key]}
                    onChange={(e) => setFormData({ ...formData, [field.key]: e.target.checked })}
                  />
                </div>
              );
            }
            if (field.field_type === 'select' && field.options) {
              return (
                <Field key={field.key} label={field.label} required={field.required}>
                  <Select
                    value={(formData[field.key] as string) ?? ''}
                    onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                  >
                    <option value="">Select…</option>
                    {field.options.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </Select>
                </Field>
              );
            }
            const secret = isSecretField(field);
            const missing = missingFields.includes(field.key);
            return (
              <Field
                key={field.key}
                label={
                  secret ? (
                    <span className="inline-flex items-center gap-1">
                      <Lock size={11} aria-hidden="true" /> {field.label}
                    </span>
                  ) : field.label
                }
                required={required}
                hint={secret && editing && !secretRequired(field) ? 'Stored value is hidden. Leave empty to keep it.' : undefined}
                error={
                  editing && secretRequired(field) && endpointChanged && !missing
                    ? 'Required because the address changed.'
                    : missing
                      ? 'Required.'
                      : undefined
                }
              >
                <Input
                  type={secret ? 'password' : field.field_type === 'url' ? 'url' : 'text'}
                  autoComplete={secret ? 'new-password' : 'off'}
                  placeholder={
                    editing && secretRequired(field)
                      ? 'required — address changed'
                      : editing && secret ? '••••••••  (unchanged)' : field.placeholder
                  }
                  value={(formData[field.key] as string) ?? ''}
                  onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                />
              </Field>
            );
          })}
          {/* Lets Enter submit the form. */}
          <button type="submit" className="sr-only" tabIndex={-1}>Submit</button>
        </form>
      </SidePanel>

      {ConfirmDialogElement}
    </div>
  );
}
