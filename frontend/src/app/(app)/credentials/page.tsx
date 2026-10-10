'use client';

import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, KeyRound, Lock, Plus, Trash2, Pencil, Shield, ShieldCheck, Terminal, MonitorDot } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { Field, Input, Select, Textarea } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { get, post, api, del } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';

/* ---------- Types ---------- */

type CredentialType = 'snmp_v2c' | 'snmp_v3' | 'winrm' | 'ssh';

interface Credential {
  id: number;
  name: string;
  type: CredentialType;
  created?: string;
}

interface CredentialForm {
  name: string;
  type: CredentialType;
  data: Record<string, string>;
}

/* ---------- Constants ---------- */

const TYPE_META: Record<CredentialType, { label: string; icon: typeof Shield }> = {
  snmp_v2c: { label: 'SNMPv2c', icon: Shield },
  snmp_v3:  { label: 'SNMPv3',  icon: ShieldCheck },
  winrm:    { label: 'WinRM',   icon: MonitorDot },
  ssh:      { label: 'SSH',     icon: Terminal },
};

const AUTH_PROTOCOLS = ['SHA', 'SHA256', 'MD5'] as const;
const PRIV_PROTOCOLS = ['AES', 'AES256', 'DES'] as const;
const TRANSPORTS = ['ntlm', 'kerberos', 'basic'] as const;

/* ---------- Field Definitions ---------- */

interface FieldDef {
  key: string;
  label: string;
  type: 'text' | 'password' | 'select' | 'textarea';
  options?: readonly string[];
  placeholder?: string;
}

const FIELDS: Record<CredentialType, FieldDef[]> = {
  snmp_v2c: [
    { key: 'community', label: 'Community String', type: 'password', placeholder: 'e.g. public' },
  ],
  snmp_v3: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'auth_protocol', label: 'Auth Protocol', type: 'select', options: AUTH_PROTOCOLS },
    { key: 'auth_password', label: 'Auth Password', type: 'password' },
    { key: 'priv_protocol', label: 'Privacy Protocol', type: 'select', options: PRIV_PROTOCOLS },
    { key: 'priv_password', label: 'Privacy Password', type: 'password' },
  ],
  winrm: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'transport', label: 'Transport', type: 'select', options: TRANSPORTS },
  ],
  ssh: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password', placeholder: 'Leave blank if using key' },
    { key: 'private_key', label: 'Private Key', type: 'textarea', placeholder: '-----BEGIN OPENSSH PRIVATE KEY-----' },
  ],
};

/* ---------- Helpers ---------- */

function emptyData(type: CredentialType): Record<string, string> {
  const data: Record<string, string> = {};
  for (const f of FIELDS[type]) {
    data[f.key] = f.type === 'select' && f.options ? f.options[0] : '';
  }
  return data;
}

/* ---------- Component ---------- */

export default function CredentialsPage() {
  useEffect(() => { document.title = 'Credentials | Nodeglow'; }, []);
  const toast = useToastStore();
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const [form, setForm] = useState<CredentialForm>({ name: '', type: 'snmp_v2c', data: emptyData('snmp_v2c') });
  const [nameError, setNameError] = useState(false);
  // Secret inputs are masked by default; the eye button reveals what is being typed.
  const [revealed, setRevealed] = useState<Set<string>>(new Set());

  function toggleReveal(key: string) {
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  /* ----- Queries ----- */

  const query = useQuery<Credential[]>({
    queryKey: ['credentials'],
    queryFn: () => get('/api/credentials/list'),
  });

  /* ----- Mutations ----- */

  const createMut = useMutation({
    mutationFn: (body: CredentialForm) => post('/api/credentials', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential created', 'success');
      closeModal();
    },
    onError: () => toast.show('Failed to create credential', 'error'),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CredentialForm }) =>
      api(`/api/credentials/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential updated', 'success');
      closeModal();
    },
    onError: () => toast.show('Failed to update credential', 'error'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => del(`/api/credentials/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential deleted', 'success');
    },
    onError: () => toast.show('Failed to delete credential', 'error'),
  });

  /* ----- Handlers ----- */

  function openCreate() {
    setEditing(null);
    setForm({ name: '', type: 'snmp_v2c', data: emptyData('snmp_v2c') });
    setModalOpen(true);
  }

  function openEdit(cred: Credential) {
    setEditing(cred);
    // Pre-fill with empty data — passwords are never returned by the API
    setForm({ name: cred.name, type: cred.type, data: emptyData(cred.type) });
    setModalOpen(true);
  }

  function closeModal() {
    setModalOpen(false);
    setEditing(null);
    setNameError(false);
    setRevealed(new Set());
  }

  function handleTypeChange(type: CredentialType) {
    setForm((f) => ({ ...f, type, data: emptyData(type) }));
  }

  function handleDataChange(key: string, value: string) {
    setForm((f) => ({ ...f, data: { ...f.data, [key]: value } }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      setNameError(true);
      return;
    }
    if (editing) {
      updateMut.mutate({ id: editing.id, body: form });
    } else {
      createMut.mutate(form);
    }
  }

  async function handleDelete(cred: Credential) {
    const ok = await confirm({ title: 'Delete credential', description: `Delete credential "${cred.name}"? This cannot be undone.`, confirmLabel: 'Delete', variant: 'danger' });
    if (!ok) return;
    deleteMut.mutate(cred.id);
  }

  const isSaving = createMut.isPending || updateMut.isPending;

  /* ----- Render ----- */

  return (
    <div>
      <PageHeader
        title="Credentials"
        description="Encrypted credentials for SNMP, WinRM and SSH. Stored secrets are never shown again."
        actions={
          <Button onClick={openCreate}>
            <Plus size={15} aria-hidden="true" />
            Add credential
          </Button>
        }
      />

      <Card padding="none">
        <QueryState
          query={query}
          errorTitle="Could not load credentials"
          loading={
            <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading credentials">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          }
          empty={
            <EmptyState
              icon={KeyRound}
              title="No credentials stored"
              description="Add your first credential to authenticate SNMP, WinRM, SSH or REST integrations. Stored values are encrypted with Fernet."
              action={
                <Button onClick={openCreate} size="sm">
                  <Plus size={14} aria-hidden="true" />
                  Add credential
                </Button>
              }
            />
          }
        >
          {(rows) => (
            <TableContainer className="relative">
              <Table className="min-w-[560px]">
                <THead>
                  <Tr>
                    <Th>Name</Th>
                    <Th>Type</Th>
                    <Th>Secret</Th>
                    <Th>Created</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((cred) => {
                    const meta = TYPE_META[cred.type] ?? { label: cred.type, icon: KeyRound };
                    const Icon = meta.icon;
                    return (
                      <Tr key={cred.id}>
                        <Td className="max-w-[280px]">
                          <span className="flex min-w-0 items-center gap-2 font-medium text-fg">
                            <KeyRound size={14} className="shrink-0 text-fg-3" aria-hidden="true" />
                            <span className="truncate">{cred.name}</span>
                          </span>
                        </Td>
                        <Td>
                          <Badge>
                            <Icon size={11} aria-hidden="true" />
                            {meta.label}
                          </Badge>
                        </Td>
                        <Td>
                          <span className="inline-flex items-center gap-1.5 text-meta text-fg-2">
                            <Lock size={12} aria-hidden="true" />
                            <span aria-hidden="true" className="font-mono tracking-wider text-fg-3">••••••••</span>
                            <span>Encrypted</span>
                          </span>
                        </Td>
                        <Td muted className="whitespace-nowrap">
                          {cred.created
                            ? new Date(cred.created).toLocaleDateString('en-US', {
                                year: 'numeric',
                                month: 'short',
                                day: 'numeric',
                              })
                            : '—'}
                        </Td>
                        <Td className="whitespace-nowrap text-right">
                          <IconButton size="sm" aria-label={`Edit ${cred.name}`} title="Edit" onClick={() => openEdit(cred)}>
                            <Pencil size={14} aria-hidden="true" />
                          </IconButton>
                          <IconButton size="sm" aria-label={`Delete ${cred.name}`} title="Delete" onClick={() => handleDelete(cred)}>
                            <Trash2 size={14} aria-hidden="true" />
                          </IconButton>
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>

      {/* ----- Add / Edit Modal ----- */}
      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={editing ? `Edit ${editing.name}` : 'Add credential'}
        description={editing ? 'Stored secrets are not shown. Leave secret fields blank to keep the existing values.' : 'Secrets are encrypted at rest and cannot be viewed after saving.'}
        footer={
          <>
            <Button type="button" variant="secondary" onClick={closeModal}>
              Cancel
            </Button>
            <Button type="submit" form="credential-form" loading={isSaving}>
              {isSaving ? 'Saving…' : editing ? 'Update' : 'Create'}
            </Button>
          </>
        }
      >
        <form id="credential-form" onSubmit={handleSubmit} className="space-y-4" noValidate>
          <Field label="Name" required error={nameError ? 'Name is required.' : undefined}>
            <Input
              value={form.name}
              onChange={(e) => { setForm((f) => ({ ...f, name: e.target.value })); setNameError(false); }}
              placeholder="e.g. Core switch SNMP"
            />
          </Field>

          <Field label="Type">
            <Select
              value={form.type}
              onChange={(e) => handleTypeChange(e.target.value as CredentialType)}
            >
              {(Object.keys(TYPE_META) as CredentialType[]).map((t) => (
                <option key={t} value={t}>
                  {TYPE_META[t].label}
                </option>
              ))}
            </Select>
          </Field>

          <div className="border-t border-border" />

          {FIELDS[form.type].map((field) => {
            if (field.type === 'select' && field.options) {
              return (
                <Field key={field.key} label={field.label}>
                  <Select
                    value={form.data[field.key] ?? ''}
                    onChange={(e) => handleDataChange(field.key, e.target.value)}
                  >
                    {field.options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </Select>
                </Field>
              );
            }
            const secret = field.type === 'password' || field.key === 'private_key';
            const shown = revealed.has(field.key);
            const placeholder = editing && secret ? '••••••••  (unchanged)' : field.placeholder;
            return (
              <div key={field.key} className="relative">
                <Field label={field.label} hint={editing && secret ? 'Stored value hidden. Leave blank to keep it.' : undefined}>
                  {field.type === 'textarea' ? (
                    <Textarea
                      className={cn('min-h-[100px] pr-10 font-mono text-meta', secret && !shown && '[-webkit-text-security:disc]')}
                      value={form.data[field.key] ?? ''}
                      onChange={(e) => handleDataChange(field.key, e.target.value)}
                      placeholder={placeholder}
                      rows={4}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  ) : (
                    <Input
                      className={secret ? 'pr-10' : undefined}
                      type={secret && !shown ? 'password' : 'text'}
                      value={form.data[field.key] ?? ''}
                      onChange={(e) => handleDataChange(field.key, e.target.value)}
                      autoComplete={secret ? 'new-password' : 'off'}
                      placeholder={placeholder}
                    />
                  )}
                </Field>
                {secret && (
                  <IconButton
                    size="sm"
                    className="absolute right-1 top-[26px]"
                    aria-label={shown ? `Hide ${field.label}` : `Show ${field.label}`}
                    aria-pressed={shown}
                    onClick={() => toggleReveal(field.key)}
                  >
                    {shown ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
                  </IconButton>
                )}
              </div>
            );
          })}
        </form>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
