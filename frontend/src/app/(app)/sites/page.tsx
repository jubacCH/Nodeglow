'use client';

import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { MapPin, Pencil, Plus, Trash2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button, IconButton } from '@/components/ui/Button';
import { Field, Input } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { Tag } from '@/components/ui/Tag';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { TableContainer, Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import { SITES_KEY, useSites, type Site } from '@/hooks/queries/useSites';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, del, patch, post } from '@/lib/api';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

/**
 * Sites (B-14): network locations inside the tenant. A host's site is its own,
 * else the site of the probe that checks it, else the default site. Reading
 * is open to everybody; creating, renaming and deleting is for admins.
 */
export default function SitesPage() {
  useEffect(() => { document.title = 'Sites | Nodeglow'; }, []);
  const query = useSites();
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [editing, setEditing] = useState<Site | 'new' | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: SITES_KEY });
    qc.invalidateQueries({ queryKey: ['hosts-v1'] });
    qc.invalidateQueries({ queryKey: ['agents'] });
  };

  async function remove(site: Site) {
    const ok = await confirm({
      title: `Delete ${site.name}?`,
      description: `Its ${site.host_count} host${site.host_count === 1 ? '' : 's'} and ${site.probe_count} probe${site.probe_count === 1 ? '' : 's'} move to the default site. Nothing is deleted besides the site itself.`,
      confirmLabel: 'Delete site',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/v1/sites/${site.id}`);
      refresh();
      toast(`${site.name} deleted, its members are in the default site`, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not delete the site'), 'error');
    }
  }

  return (
    <div>
      <PageHeader
        title="Sites"
        description={`Where your hosts are. A host follows the site of its probe unless it has its own. ${formatAsOf(query.dataUpdatedAt) ?? ''}`.trim()}
        actions={isAdmin && (
          <Button onClick={() => setEditing('new')}>
            <Plus size={15} aria-hidden="true" /> Add site
          </Button>
        )}
      />
      <Card>
        <QueryState
          query={query}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState icon={MapPin} title="No sites yet" description="Every installation has a default site; it appears here once the server reports it." />}
        >
          {(sites) => (
            <TableContainer>
              <Table aria-label="Sites">
                <THead>
                  <Tr>
                    <Th>Name</Th>
                    <Th numeric>Hosts</Th>
                    <Th numeric>Probes</Th>
                    {isAdmin && <Th className="text-right"><span className="sr-only">Actions</span></Th>}
                  </Tr>
                </THead>
                <TBody>
                  {sites.map((s) => (
                    <Tr key={s.id}>
                      <Td>
                        <span className="flex items-center gap-2">
                          <span className="font-medium">{s.name}</span>
                          {s.is_default && <Tag>Default</Tag>}
                        </span>
                      </Td>
                      <Td numeric>
                        <Link href={`/hosts?site=${s.id}`} className="num text-accent hover:text-accent-hover">{s.host_count}</Link>
                      </Td>
                      <Td numeric className="num">{s.probe_count}</Td>
                      {isAdmin && (
                        <Td className="text-right">
                          <span className="inline-flex gap-1">
                            <IconButton size="sm" aria-label={`Rename ${s.name}`} title="Rename" onClick={() => setEditing(s)}>
                              <Pencil size={14} aria-hidden="true" />
                            </IconButton>
                            {!s.is_default && (
                              <IconButton size="sm" aria-label={`Delete ${s.name}`} title="Delete" onClick={() => remove(s)}>
                                <Trash2 size={14} aria-hidden="true" />
                              </IconButton>
                            )}
                          </span>
                        </Td>
                      )}
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </Card>
      <p className="mt-3 text-meta text-fg-3">
        Assign hosts on the Hosts page (Move to site, or the host&apos;s Edit dialog) and probes on the Agents page.
        Sites only group and filter; syslog matching per site comes later.
      </p>
      <SiteDialog
        site={editing}
        onClose={() => setEditing(null)}
        onSaved={(name, created) => {
          refresh();
          toast(created ? `${name} added` : `Renamed to ${name}`, 'success');
        }}
      />
      {ConfirmDialogElement}
    </div>
  );
}

function SiteDialog({ site, onClose, onSaved }: {
  site: Site | 'new' | null;
  onClose: () => void;
  onSaved: (name: string, created: boolean) => void;
}) {
  const creating = site === 'new';
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (site) {
      setName(site === 'new' ? '' : site.name);
      setError(null);
    }
  }, [site]);

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    const value = name.trim();
    if (!value) { setError('Enter a name.'); return; }
    if (value.length > 128) { setError('Use at most 128 characters.'); return; }
    setSaving(true);
    try {
      if (creating) await post('/api/v1/sites', { name: value });
      else if (site) await patch(`/api/v1/sites/${site.id}`, { name: value });
      onSaved(value, creating);
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save the site.'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={site !== null}
      onClose={onClose}
      title={creating ? 'Add site' : 'Rename site'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="site-form" loading={saving}>{creating ? 'Add' : 'Save'}</Button>
        </>
      }
    >
      <form id="site-form" onSubmit={submit} noValidate>
        <Field label="Name" error={error ?? undefined}>
          <Input value={name} onChange={(e) => { setName(e.target.value); setError(null); }} placeholder="Bern branch" />
        </Field>
      </form>
    </Modal>
  );
}
