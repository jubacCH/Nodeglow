'use client';

import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Key, Plus, Trash2 } from 'lucide-react';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Input, Select } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { useConfirm } from '@/hooks/useConfirm';
import { del, get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { Code, Notice, SettingsSection } from './formKit';

interface ApiKeyEntry {
  id: number;
  name: string;
  prefix: string;
  role: string;
  enabled: boolean;
  last_used: string | null;
  created_at: string | null;
}

interface ApiEndpoint {
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  path: string;
  desc: string;
}

// Methods as neutral/accent labels: status colours are reserved for state.
const METHOD_TONE: Record<ApiEndpoint['method'], BadgeTone> = {
  GET: 'neutral',
  POST: 'accent',
  PATCH: 'accent',
  DELETE: 'down',
};

const ROLE_TONE: Record<string, BadgeTone> = { admin: 'accent', editor: 'neutral', readonly: 'neutral' };

const DOCS: { title: string; endpoints: ApiEndpoint[] }[] = [
  { title: 'Hosts', endpoints: [
    { method: 'GET', path: '/api/v1/hosts', desc: 'List all hosts with current status' },
    { method: 'GET', path: '/api/v1/hosts/{id}', desc: 'Host detail with metrics, uptime, agent data' },
    { method: 'GET', path: '/api/v1/hosts/{id}/history?hours=24', desc: 'Ping history for a host' },
    { method: 'POST', path: '/api/v1/hosts', desc: 'Create a new host (name, hostname, check_type, port)' },
    { method: 'PATCH', path: '/api/v1/hosts/{id}', desc: 'Update host (name, hostname, check_type, port, enabled)' },
    { method: 'DELETE', path: '/api/v1/hosts/{id}', desc: 'Delete a host and its ping results' },
  ] },
  { title: 'Agents', endpoints: [
    { method: 'GET', path: '/api/v1/agents', desc: 'List all registered agents' },
    { method: 'GET', path: '/api/v1/agents/{id}', desc: 'Agent detail with performance snapshots' },
    { method: 'DELETE', path: '/api/v1/agents/{id}', desc: 'Decommission agent (removes host + snapshots)' },
  ] },
  { title: 'Integrations', endpoints: [
    { method: 'GET', path: '/api/v1/integrations', desc: 'List all integration instances with status' },
    { method: 'GET', path: '/api/v1/integrations/{id}', desc: 'Integration detail with latest snapshot' },
  ] },
  { title: 'Incidents', endpoints: [
    { method: 'GET', path: '/api/v1/incidents', desc: 'List incidents (filter: ?status=open)' },
    { method: 'GET', path: '/api/v1/incidents/{id}', desc: 'Incident detail with event timeline' },
    { method: 'POST', path: '/api/v1/incidents/{id}/acknowledge', desc: 'Acknowledge an incident' },
    { method: 'POST', path: '/api/v1/incidents/{id}/resolve', desc: 'Resolve an incident' },
  ] },
  { title: 'Rules', endpoints: [
    { method: 'GET', path: '/api/v1/rules', desc: 'List all alert rules' },
    { method: 'POST', path: '/api/v1/rules/{id}/toggle', desc: 'Enable/disable a rule' },
    { method: 'POST', path: '/api/v1/rules/{id}/delete', desc: 'Delete a rule' },
  ] },
  { title: 'Syslog', endpoints: [
    { method: 'GET', path: '/api/v1/syslog', desc: 'Query syslog (?severity=3&host_id=1&limit=100&hours=24)' },
  ] },
  { title: 'System', endpoints: [
    { method: 'GET', path: '/api/v1/status', desc: 'System status overview' },
    { method: 'GET', path: '/api/v1/keys', desc: 'List API keys (admin only)' },
    { method: 'POST', path: '/api/v1/keys', desc: 'Create API key (admin only)' },
    { method: 'DELETE', path: '/api/v1/keys/{id}', desc: 'Delete API key (admin only)' },
  ] },
];

function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

export function ApiTab() {
  const toast = useToastStore();
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [role, setRole] = useState<'readonly' | 'editor' | 'admin'>('readonly');
  const [nameError, setNameError] = useState('');
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const keys = useQuery<ApiKeyEntry[]>({
    queryKey: ['api-keys'],
    queryFn: () => get('/settings/api-keys'),
  });

  const createMut = useMutation({
    mutationFn: (body: { name: string; role: string }) =>
      post<{ ok: boolean; key: string; id: number; prefix: string }>('/settings/api-keys/create', body),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      toast.show('API key created', 'success');
    },
    onError: () => toast.show('Failed to create API key', 'error'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => del(`/settings/api-keys/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      toast.show('API key deleted', 'success');
    },
    onError: () => toast.show('Failed to delete API key', 'error'),
  });

  function closeModal() {
    setModalOpen(false);
    setCreatedKey(null);
    setName('');
    setRole('readonly');
    setNameError('');
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setNameError('Name is required');
      nameRef.current?.focus();
      return;
    }
    createMut.mutate({ name, role });
  }

  async function handleDelete(k: ApiKeyEntry) {
    const ok = await confirm({
      title: 'Delete API key',
      description: `Delete API key "${k.name}"? Integrations using it stop working immediately. This cannot be undone.`,
      confirmLabel: 'Delete key',
      variant: 'danger',
    });
    if (ok) deleteMut.mutate(k.id);
  }

  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://your-instance';

  return (
    <div className="space-y-4">
      <SettingsSection
        id="api-keys"
        title="API keys"
        description={<>Keys for programmatic access. They start with <Code>ng_</Code> and are sent in the <Code>X-API-Key</Code> header.</>}
        actions={
          <Button size="sm" onClick={() => setModalOpen(true)}>
            <Plus size={14} aria-hidden="true" /> Create key
          </Button>
        }
      >
        <QueryState
          query={keys}
          compact
          loading={<div className="space-y-2" aria-busy="true" aria-label="Loading">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9 w-full" />)}</div>}
          empty={
            <EmptyState
              compact
              icon={Key}
              title="No API keys yet"
              description="Create a key to integrate Nodeglow with external systems."
            />
          }
        >
          {(rows) => (
            <TableContainer>
              <Table>
                <THead>
                  <Tr>
                    <Th>Name</Th>
                    <Th>Prefix</Th>
                    <Th>Role</Th>
                    <Th className="max-sm:hidden">Created</Th>
                    <Th className="max-sm:hidden">Last used</Th>
                    <Th className="text-right"><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((k) => (
                    <Tr key={k.id}>
                      <Td className="font-medium">{k.name}</Td>
                      <Td><code className="font-mono text-meta text-fg-2">{k.prefix}…</code></Td>
                      <Td><Badge tone={ROLE_TONE[k.role] ?? 'neutral'}>{k.role}</Badge></Td>
                      <Td muted className="num text-meta max-sm:hidden">{k.created_at ? new Date(k.created_at).toLocaleDateString() : '—'}</Td>
                      <Td muted className="num text-meta max-sm:hidden">{k.last_used ? new Date(k.last_used).toLocaleDateString() : 'Never'}</Td>
                      <Td className="text-right">
                        <IconButton size="sm" aria-label={`Delete API key ${k.name}`} onClick={() => handleDelete(k)} className="hover:text-down">
                          <Trash2 size={14} aria-hidden="true" />
                        </IconButton>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </SettingsSection>

      <SettingsSection
        id="api-docs"
        title="API reference"
        description={<>All endpoints are under <Code>/api/v1/</Code> and require the <Code>X-API-Key</Code> header.</>}
      >
        <div className="grid gap-5 lg:grid-cols-2">
          {DOCS.map((section) => (
            <div key={section.title} className="min-w-0">
              <h3 className="mb-1.5 text-meta font-medium text-fg-2">{section.title}</h3>
              <ul className="space-y-1">
                {section.endpoints.map((ep) => (
                  <li key={`${ep.method}-${ep.path}`} className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 py-0.5">
                    <Badge tone={METHOD_TONE[ep.method]} className="w-[52px] justify-center font-mono">{ep.method}</Badge>
                    <code className="break-all font-mono text-meta text-fg">{ep.path}</code>
                    <span className="text-meta text-fg-3">{ep.desc}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-5 rounded-ctl border border-border bg-surface-2 p-3">
          <p className="mb-1 text-meta font-medium text-fg-2">Example request</p>
          <pre className="overflow-x-auto font-mono text-meta text-fg">
{`curl -H "X-API-Key: ng_your_key_here" \\
  ${origin}/api/v1/hosts`}
          </pre>
        </div>
      </SettingsSection>

      <Modal
        open={modalOpen}
        onClose={closeModal}
        title={createdKey ? 'API key created' : 'Create API key'}
        initialFocus={createdKey ? undefined : nameRef}
      >
        {createdKey ? (
          <div className="space-y-4">
            <Notice tone="warning">Copy the key now. It will not be shown again.</Notice>
            <div className="flex items-center gap-2 rounded-ctl border border-border-2 bg-surface-2 p-3">
              <code className="flex-1 break-all font-mono text-ui text-fg">{createdKey}</code>
              <IconButton
                size="sm"
                aria-label="Copy API key"
                onClick={() => { copyText(createdKey); toast.show('Copied to clipboard', 'success'); }}
              >
                <Copy size={14} aria-hidden="true" />
              </IconButton>
            </div>
            <div className="flex justify-end">
              <Button size="sm" onClick={closeModal}>Done</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-4" noValidate>
            <Field label="Key name" required error={nameError || undefined}>
              <Input
                ref={nameRef}
                value={name}
                onChange={(e) => { setName(e.target.value); setNameError(''); }}
                placeholder="e.g. Grafana readonly"
              />
            </Field>
            <Field label="Role" hint="Use the lowest role that works; read-only keys cannot change anything.">
              <Select value={role} onChange={(e) => setRole(e.target.value as 'readonly' | 'editor' | 'admin')}>
                <option value="readonly">Read-only</option>
                <option value="editor">Editor</option>
                <option value="admin">Admin</option>
              </Select>
            </Field>
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" size="sm" onClick={closeModal}>Cancel</Button>
              <Button type="submit" size="sm" loading={createMut.isPending}>Create key</Button>
            </div>
          </form>
        )}
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
