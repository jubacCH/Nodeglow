'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Pause, Play, Plus, ShieldOff, Trash2, Undo2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Notice } from '@/components/settings/formKit';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Input, Select } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { Tag } from '@/components/ui/Tag';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, get, patch, post } from '@/lib/api';
import {
  deletionConfirmed, formatDate, purgeDate, tenantStatusLabel, tenantStatusTone, usageLabel,
  type TenantRow, type TenantsPayload,
} from '@/lib/tenants';
import { useAuthStore, useIsOperator } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

const TENANTS_KEY = ['tenants'] as const;

function ErrorNote({ children }: { children: string }) {
  return <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{children}</p>;
}

/**
 * Tenants (installation operators, ee multi-tenancy). Lists every tenant with
 * its status, plan and member count; creates, suspends, resumes and
 * schedules the deletion of tenants (30 days suspended, then purged), and
 * assigns retention plans. Operators see no tenant data here — that needs
 * the tenant's support access.
 */
export default function TenantsPage() {
  useEffect(() => { document.title = 'Tenants | Nodeglow'; }, []);
  const isOperator = useIsOperator();
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<TenantRow | null>(null);

  const query = useQuery({
    queryKey: TENANTS_KEY,
    queryFn: () => get<TenantsPayload>('/api/tenants'),
    enabled: isOperator,
  });

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: TENANTS_KEY });
    await fetchUser(); // the switcher's list (suspended tenants drop out)
  };

  async function act(t: TenantRow, path: string, done: string, body?: unknown) {
    try {
      await post(`/api/tenants/${t.id}/${path}`, body);
      await refresh();
      toast(done, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'The action failed'), 'error');
    }
  }

  async function suspend(t: TenantRow) {
    const ok = await confirm({
      title: `Suspend ${t.name}?`,
      description: 'Monitoring of this tenant stops, nobody can sign in to it, and its API keys and agents stop working. Nothing is deleted; you can resume it at any time.',
      confirmLabel: 'Suspend tenant',
      variant: 'danger',
    });
    if (ok) await act(t, 'suspend', `${t.name} suspended`);
  }

  async function setPlan(t: TenantRow, plan: string) {
    try {
      await patch(`/api/tenants/${t.id}`, { plan });
      await refresh();
      toast(`Plan of ${t.name} changed`, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'The plan could not be changed'), 'error');
    }
  }

  if (!isOperator) {
    return (
      <div>
        <PageHeader title="Tenants" description="Customers on this installation" />
        <Card>
          <EmptyState icon={ShieldOff} title="Operator access required"
                      description="Only the operators of this installation manage tenants." />
        </Card>
      </div>
    );
  }

  const data = query.data;
  const problems = data?.creation_problems ?? [];

  return (
    <div>
      <PageHeader
        title="Tenants"
        description={data ? usageLabel(data.usage) : 'Customers on this installation'}
        actions={
          <Button onClick={() => setCreating(true)} disabled={!data || problems.length > 0}>
            <Plus size={16} aria-hidden="true" /> New tenant
          </Button>
        }
      />
      {problems.length > 0 && (
        <Notice tone="warning" className="mb-4">
          <span className="font-medium">A new tenant cannot be created yet.</span>{' '}
          {problems.join(' ')}
        </Notice>
      )}

      <Card padding="none">
        <QueryState
          query={query}
          errorTitle="Could not load the tenants"
          loading={
            <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
              {[0, 1, 2].map((i) => <Skeleton key={i} className="h-9 w-full" />)}
            </div>
          }
          isEmpty={(d) => d.tenants.length === 0}
          empty={<EmptyState icon={Building2} title="No tenants" />}
        >
          {(d) => (
            <TableContainer>
              <Table>
                <THead>
                  <Tr>
                    <Th className="pl-5">Status</Th>
                    <Th>Tenant</Th>
                    <Th>Plan</Th>
                    <Th numeric className="max-sm:hidden">Members</Th>
                    <Th className="max-md:hidden">Created</Th>
                    <Th className="pr-5 text-right"><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {d.tenants.map((t) => (
                    <Tr key={t.id}>
                      <Td className="pl-5">
                        <StatusPill status={tenantStatusTone(t.status)} size="sm">{tenantStatusLabel(t)}</StatusPill>
                        {t.status === 'deleting' && t.delete_after && (
                          <span className="mt-0.5 block text-meta text-fg-3">purge {formatDate(t.delete_after)}</span>
                        )}
                      </Td>
                      <Td>
                        <span className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="font-medium">{t.name}</span>
                          {t.default && <Tag>Default</Tag>}
                        </span>
                        <span className="block font-mono text-meta text-fg-3">{t.slug}</span>
                      </Td>
                      <Td>
                        <Select
                          aria-label={`Plan of ${t.name}`}
                          value={t.plan ?? ''}
                          onChange={(e) => setPlan(t, e.target.value)}
                          disabled={!d.usage.licensed || t.status === 'deleting'}
                          className="h-[30px] min-h-0 w-[190px] py-0 text-meta"
                        >
                          {!t.plan && <option value="">No plan (installation caps)</option>}
                          {d.plans.map((p) => (
                            <option key={p.key} value={p.key}>{p.name} · {p.retention_days} days</option>
                          ))}
                        </Select>
                      </Td>
                      <Td numeric muted className="max-sm:hidden">{t.members ?? '—'}</Td>
                      <Td muted className="num text-meta max-md:hidden">{formatDate(t.created_at)}</Td>
                      <Td className="pr-5">
                        <div className="flex flex-wrap items-center justify-end gap-1">
                          {!t.default && t.status === 'active' && (
                            <Button size="sm" variant="ghost" onClick={() => suspend(t)}>
                              <Pause size={14} aria-hidden="true" /> Suspend
                            </Button>
                          )}
                          {t.status === 'suspended' && (
                            <Button size="sm" variant="ghost" onClick={() => act(t, 'resume', `${t.name} resumed`)}>
                              <Play size={14} aria-hidden="true" /> Resume
                            </Button>
                          )}
                          {t.status === 'deleting' && !t.purge_started_at && (
                            <Button size="sm" variant="ghost" onClick={() => act(t, 'cancel-deletion', `Deletion of ${t.name} cancelled; it stays suspended`)}>
                              <Undo2 size={14} aria-hidden="true" /> Cancel deletion
                            </Button>
                          )}
                          {!t.default && t.status !== 'deleting' && (
                            <Button size="sm" variant="danger" onClick={() => setDeleting(t)} aria-label={`Delete ${t.name}`}>
                              <Trash2 size={14} aria-hidden="true" /> Delete
                            </Button>
                          )}
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>
      <p className="mt-3 text-meta text-fg-3">
        Plans cap every retention setting of the tenant; its admins choose within the plan. Data the storage keeps
        for a fixed time (ping results 30 days, agent and traffic metrics 7 days) is not extended by a larger plan.
      </p>

      {data && (
        <CreateTenantModal
          open={creating}
          onClose={() => setCreating(false)}
          payload={data}
          onCreated={async (name) => { setCreating(false); await refresh(); toast(`${name} created`, 'success'); }}
        />
      )}
      {data && (
        <DeleteTenantModal
          tenant={deleting}
          graceDays={data.deletion_grace_days}
          onClose={() => setDeleting(null)}
          onDeleted={async (name) => { setDeleting(null); await refresh(); toast(`${name} is scheduled for deletion`, 'success'); }}
        />
      )}
      {ConfirmDialogElement}
    </div>
  );
}

function CreateTenantModal({
  open, onClose, payload, onCreated,
}: { open: boolean; onClose: () => void; payload: TenantsPayload; onCreated: (name: string) => Promise<void> }) {
  const empty = { name: '', slug: '', plan: payload.default_plan, admin_username: '', admin_password: '' };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  function close() {
    setForm(empty);
    setError('');
    onClose();
  }

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    setError('');
    if (!form.name.trim() || !form.admin_username.trim()) {
      setError('Name the tenant and its first admin.');
      return;
    }
    setSaving(true);
    try {
      await post('/api/tenants', {
        ...form,
        slug: form.slug.trim() || undefined,
        admin_password: form.admin_password || undefined,
      });
      const name = form.name.trim();
      setForm(empty);
      await onCreated(name);
    } catch (err) {
      setError(apiErrorMessage(err, 'The tenant could not be created'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={close} title="New tenant" initialFocus={nameRef}
           description="A customer with its own hosts, users, settings and data. You get no access to it yourself.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field label="Name" required>
          <Input ref={nameRef} value={form.name} maxLength={128} autoComplete="off"
                 onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Short name" hint="a-z, 0-9 and '-'; from the name when empty">
          <Input value={form.slug} maxLength={64} autoComplete="off" className="font-mono"
                 onChange={(e) => setForm({ ...form, slug: e.target.value })} />
        </Field>
        <Field label="Plan" hint="Caps the tenant's retention settings.">
          <Select value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value })}>
            {payload.plans.map((p) => <option key={p.key} value={p.key}>{p.name} · {p.retention_days} days</option>)}
          </Select>
        </Field>
        <Field label="First admin" required hint="An existing user becomes the tenant's admin; a new name creates a local user.">
          <Input value={form.admin_username} maxLength={64} autoComplete="off"
                 onChange={(e) => setForm({ ...form, admin_username: e.target.value })} />
        </Field>
        <Field label="Password for a new admin user" hint="Not needed for an existing user.">
          <Input type="password" value={form.admin_password} autoComplete="new-password"
                 onChange={(e) => setForm({ ...form, admin_password: e.target.value })} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={close}>Cancel</Button>
          <Button type="submit" size="sm" loading={saving}>Create tenant</Button>
        </div>
      </form>
    </Modal>
  );
}

function DeleteTenantModal({
  tenant, graceDays, onClose, onDeleted,
}: { tenant: TenantRow | null; graceDays: number; onClose: () => void; onDeleted: (name: string) => Promise<void> }) {
  const [typed, setTyped] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const name = tenant?.name ?? '';
  const ok = deletionConfirmed(typed, name);
  const purge = purgeDate(new Date(), graceDays);

  function close() {
    setTyped('');
    setError('');
    onClose();
  }

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!tenant || !ok) return;
    setSaving(true);
    setError('');
    try {
      await post(`/api/tenants/${tenant.id}/delete`, { confirm: typed.trim() });
      setTyped('');
      await onDeleted(name);
    } catch (err) {
      setError(apiErrorMessage(err, 'The deletion could not be scheduled'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={!!tenant} onClose={close} title={`Delete ${name}?`} initialFocus={inputRef}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p className="text-ui leading-relaxed text-fg-2">
          The tenant is suspended now and <b className="font-medium text-fg">purged on {formatDate(purge.toISOString())}</b>{' '}
          ({graceDays} days from today): every host, incident, log line, metric, setting and user of this tenant
          only is deleted for good. Until then the deletion can be cancelled.
        </p>
        <Field label={<>Type <span className="font-mono">{name}</span> to confirm</>}>
          <Input ref={inputRef} value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={close}>Cancel</Button>
          <Button type="submit" size="sm" variant="danger" loading={saving} disabled={!ok || saving}>
            Schedule deletion
          </Button>
        </div>
      </form>
    </Modal>
  );
}
