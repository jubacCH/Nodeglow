'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, ShieldOff, Trash2, Users } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button, IconButton } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Input, Select } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { Tag } from '@/components/ui/Tag';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorBody, apiErrorMessage, del, get, patch, post } from '@/lib/api';
import { useIsAdmin, useUser } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

interface UserInfo {
  id: number;
  username: string;
  role: string;
  auth_source: string;
  display_name: string | null;
  created_at: string;
}

const ROLES = ['admin', 'editor', 'readonly'] as const;
const ROLE_HINT = 'Admin: everything incl. settings and users · Editor: change monitoring objects · Read-only: view only';

function ErrorNote({ children }: { children: string }) {
  return (
    <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{children}</p>
  );
}

export default function UsersPage() {
  useEffect(() => { document.title = 'Users | Nodeglow'; }, []);
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [resetPwUser, setResetPwUser] = useState<UserInfo | null>(null);
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'readonly' });
  const [newPw, setNewPw] = useState('');
  const [currentPw, setCurrentPw] = useState('');
  const [pwError, setPwError] = useState('');
  const me = useUser();
  // Changing your own password needs the current one; an admin resetting
  // someone else's does not.
  const resetIsSelf = !!resetPwUser && !!me && resetPwUser.id === me.id;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  const usernameRef = useRef<HTMLInputElement>(null);
  const currentPwRef = useRef<HTMLInputElement>(null);
  const newPwRef = useRef<HTMLInputElement>(null);

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => get<UserInfo[]>('/api/users'),
    enabled: isAdmin,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] });

  function closeAdd() {
    setShowAdd(false);
    setError('');
  }

  async function handleCreate(e?: FormEvent) {
    e?.preventDefault();
    setError('');
    if (!newUser.username.trim() || !newUser.password) {
      setError('Username and password required');
      return;
    }
    setSaving(true);
    try {
      await post('/api/users', newUser);
      setShowAdd(false);
      setNewUser({ username: '', password: '', role: 'readonly' });
      refresh();
      toast('User created', 'success');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create user');
    } finally {
      setSaving(false);
    }
  }

  async function handleRoleChange(userId: number, role: string) {
    try {
      await patch(`/api/users/${userId}`, { role });
      refresh();
      toast('Role updated', 'success');
    } catch {
      toast('Failed to update role', 'error');
    }
  }

  async function handleDelete(u: UserInfo) {
    const ok = await confirm({
      title: 'Delete user',
      description: `Delete "${u.username}"? They are signed out and cannot log in again. This cannot be undone.`,
      confirmLabel: 'Delete user',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/users/${u.id}`);
      refresh();
      toast('User deleted', 'success');
    } catch {
      toast('Failed to delete user', 'error');
    }
  }

  function openResetPassword(u: UserInfo | null) {
    setResetPwUser(u);
    setNewPw('');
    setCurrentPw('');
    setPwError('');
  }

  async function handleResetPassword(e?: FormEvent) {
    e?.preventDefault();
    if (!resetPwUser || !newPw) return;
    if (resetIsSelf && !currentPw) {
      setPwError('Enter your current password.');
      return;
    }
    setSaving(true);
    setPwError('');
    try {
      await patch(
        `/api/users/${resetPwUser.id}`,
        resetIsSelf ? { password: newPw, current_password: currentPw } : { password: newPw },
      );
      openResetPassword(null);
      toast(resetIsSelf ? 'Password changed' : 'Password reset', 'success');
    } catch (err) {
      const body = apiErrorBody(err);
      setPwError(
        body?.code === 'current_password_required'
          ? (body.error || 'Your current password is missing or wrong.')
          : apiErrorMessage(err, 'Failed to reset password'),
      );
    } finally {
      setSaving(false);
    }
  }

  if (!isAdmin) {
    return (
      <div>
        <PageHeader title="Users" description="Accounts and roles" />
        <Card>
          <EmptyState
            icon={ShieldOff}
            title="Admin access required"
            description="You need an admin role to manage users. Contact your administrator to request access."
          />
        </Card>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Users"
        description={users.data ? `${users.data.length} account${users.data.length === 1 ? '' : 's'} · local and LDAP` : 'Accounts and roles'}
        actions={
          <Button onClick={() => setShowAdd(true)}>
            <Plus size={16} aria-hidden="true" /> Add user
          </Button>
        }
      />

      <Card padding="none">
        <QueryState
          query={users}
          errorTitle="Could not load users"
          loading={
            <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
              {[0, 1, 2].map((i) => <Skeleton key={i} className="h-9 w-full" />)}
            </div>
          }
          empty={<EmptyState icon={Users} title="No users" description="Add the first account to give someone access." />}
        >
          {(rows) => (
            <TableContainer className="relative">
              <Table>
                <THead>
                  <Tr>
                    <Th className="pl-5">User</Th>
                    <Th>Role</Th>
                    <Th className="max-sm:hidden">Created</Th>
                    <Th className="pr-5 text-right"><span className="sr-only">Actions</span></Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((u) => {
                    const self = me?.id === u.id;
                    return (
                      <Tr key={u.id}>
                        <Td className="py-2 pl-5">
                          <span className="flex min-w-0 flex-wrap items-center gap-2">
                            <span className="font-medium">{u.display_name || u.username}</span>
                            {u.auth_source === 'ldap' && <Tag>LDAP</Tag>}
                            {self && <Tag>You</Tag>}
                          </span>
                          {u.display_name && u.display_name !== u.username && (
                            <span className="block font-mono text-meta text-fg-3">{u.username}</span>
                          )}
                        </Td>
                        <Td>
                          <Select
                            aria-label={`Role of ${u.username}`}
                            value={u.role}
                            onChange={(e) => handleRoleChange(u.id, e.target.value)}
                            className="h-[30px] min-h-0 w-[130px] py-0 text-meta"
                          >
                            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                          </Select>
                        </Td>
                        <Td muted className="num text-meta max-sm:hidden">
                          {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                        </Td>
                        <Td className="pr-5">
                          <div className="flex items-center justify-end gap-1">
                            <IconButton
                              size="sm"
                              onClick={() => openResetPassword(u)}
                              title={self ? 'Change my password' : 'Reset password'}
                              aria-label={self ? 'Change my password' : `Reset password for ${u.username}`}
                            >
                              <KeyRound size={14} aria-hidden="true" />
                            </IconButton>
                            <IconButton
                              size="sm"
                              onClick={() => handleDelete(u)}
                              title="Delete user"
                              aria-label={`Delete user ${u.username}`}
                              className="hover:text-down"
                            >
                              <Trash2 size={14} aria-hidden="true" />
                            </IconButton>
                          </div>
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
      <p className="mt-3 text-meta text-fg-3">{ROLE_HINT}</p>

      {/* Add user */}
      <Modal open={showAdd} onClose={closeAdd} title="Add user" initialFocus={usernameRef}>
        <form onSubmit={handleCreate} className="space-y-4" noValidate>
          {error && <ErrorNote>{error}</ErrorNote>}
          <Field label="Username" required>
            <Input
              ref={usernameRef}
              autoComplete="off"
              value={newUser.username}
              onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
            />
          </Field>
          <Field label="Password" required>
            <Input
              type="password"
              autoComplete="new-password"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
            />
          </Field>
          <Field label="Role" hint={ROLE_HINT}>
            <Select value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={closeAdd}>Cancel</Button>
            <Button type="submit" size="sm" loading={saving}>Create user</Button>
          </div>
        </form>
      </Modal>

      {/* Reset / change password */}
      <Modal
        open={!!resetPwUser}
        onClose={() => openResetPassword(null)}
        title={resetIsSelf ? 'Change my password' : `Reset password — ${resetPwUser?.username ?? ''}`}
        initialFocus={resetIsSelf ? currentPwRef : newPwRef}
      >
        <form onSubmit={handleResetPassword} className="space-y-4" noValidate>
          {pwError && <ErrorNote>{pwError}</ErrorNote>}
          {resetIsSelf && (
            <Field label="Current password">
              <Input
                ref={currentPwRef}
                id="current-password"
                type="password"
                value={currentPw}
                onChange={(e) => setCurrentPw(e.target.value)}
                autoComplete="current-password"
              />
            </Field>
          )}
          <Field label="New password">
            <Input
              ref={newPwRef}
              id="new-password"
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => openResetPassword(null)}>Cancel</Button>
            <Button type="submit" size="sm" loading={saving} disabled={saving || !newPw || (resetIsSelf && !currentPw)}>
              {resetIsSelf ? 'Change password' : 'Reset password'}
            </Button>
          </div>
        </form>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
