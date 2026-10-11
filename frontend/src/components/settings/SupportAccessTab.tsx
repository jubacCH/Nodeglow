'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Field, Select, Textarea } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { SUPPORT_ACCESS_KEY, useSupportAccess } from '@/hooks/queries/useSupportAccess';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, del, post } from '@/lib/api';
import type { HealthState } from '@/lib/status';
import { durationOptions, formatDateTime, type SupportGrant, type SupportState } from '@/lib/tenants';
import { useToastStore } from '@/stores/toast';
import { Notice, SettingsSection } from './formKit';

const STATUS: Record<SupportGrant['status'], { state: HealthState; label: string }> = {
  active: { state: 'warning', label: 'Active' },
  expired: { state: 'unknown', label: 'Expired' },
  revoked: { state: 'maint', label: 'Revoked' },
};

const ROLE_HINT: Record<string, string> = {
  readonly: 'Read-only: the operators can look, not change.',
  editor: 'Editor: the operators can also change monitoring objects.',
  admin: 'Admin: the operators can also change settings and users of this tenant.',
};

/**
 * Settings → Support access (owner decision 8). The operators of this
 * installation see nothing of this tenant unless an admin grants access here:
 * for a limited time, with a reason, revocable at any moment. Every access
 * appears in the audit log as "support".
 */
export function SupportAccessTab() {
  const query = useSupportAccess();
  return (
    <QueryState query={query} errorTitle="Could not load support access">
      {(state) => <SupportAccess state={state} />}
    </QueryState>
  );
}

function SupportAccess({ state }: { state: SupportState }) {
  const qc = useQueryClient();
  const toast = useToastStore();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const options = durationOptions(state.limits);
  const [role, setRole] = useState('readonly');
  const [hours, setHours] = useState(options.find((o) => o.hours === 24)?.hours ?? options[0]?.hours ?? 24);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: SUPPORT_ACCESS_KEY });

  async function grant() {
    setBusy(true);
    setError(null);
    try {
      await post('/api/support-access', { role, hours, reason: reason.trim() });
      setReason('');
      await refresh();
      toast.show('Support access granted', 'success');
    } catch (e) {
      setError(apiErrorMessage(e, 'Support access could not be granted'));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(g: SupportGrant) {
    const ok = await confirm({
      title: 'End support access?',
      description: `The operators lose access to this tenant at once ("${g.reason}").`,
      confirmLabel: 'End access',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/support-access/${g.id}`);
      await refresh();
      toast.show('Support access ended', 'success');
    } catch (e) {
      toast.show(apiErrorMessage(e, 'Support access could not be ended'), 'error');
    }
  }

  return (
    <div className="space-y-5">
      <SettingsSection
        id="support"
        title="Support access"
        description="The operators of this installation have no access to this tenant's data. Grant it here when you need their help: for a limited time, with a reason. Every access is recorded in the audit log."
        actions={state.active.length > 0
          ? <StatusPill status="warning">{state.active.length} active</StatusPill>
          : <StatusPill status="unknown" noDot>None active</StatusPill>}
      >
        {state.via_support && (
          <Notice tone="info" className="mb-4">
            You are working here through support access; it cannot grant or extend support access.
          </Notice>
        )}
        {state.can_manage && !state.can_grant && !state.via_support && (
          <Notice tone="warning" className="mb-4">
            New support access needs a valid Nodeglow Enterprise license. Active access can still be ended.
          </Notice>
        )}
        {state.can_grant && (
          <form
            className="grid max-w-3xl grid-cols-2 gap-4 max-[640px]:grid-cols-1"
            onSubmit={(e) => { e.preventDefault(); void grant(); }}
            noValidate
          >
            <Field label="Role for the operators" hint={ROLE_HINT[role]}>
              <Select value={role} onChange={(e) => setRole(e.target.value)}>
                {state.limits.roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </Select>
            </Field>
            <Field label="Duration">
              <Select value={String(hours)} onChange={(e) => setHours(Number(e.target.value))}>
                {options.map((o) => <option key={o.hours} value={o.hours}>{o.label}</option>)}
              </Select>
            </Field>
            <Field label="Reason" required error={error} className="col-span-2 max-[640px]:col-span-1"
                   hint="E.g. the ticket number. Shown to every member while access is active.">
              <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500} />
            </Field>
            <div className="col-span-2 flex justify-end max-[640px]:col-span-1">
              <Button type="submit" loading={busy} disabled={busy || reason.trim().length < 3}>
                Grant support access
              </Button>
            </div>
          </form>
        )}
      </SettingsSection>

      <SettingsSection id="support-history" title="Grants" description="The last 50 grants of this tenant.">
        {state.history.length === 0 && state.active.length === 0 ? (
          <EmptyState variant="not-configured" title="No support access granted yet" />
        ) : (
          <TableContainer>
            <Table density="compact">
              <THead>
                <Tr>
                  <Th>Status</Th>
                  <Th>Reason</Th>
                  <Th>Role</Th>
                  <Th className="max-sm:hidden">Granted</Th>
                  <Th>Until</Th>
                  <Th><span className="sr-only">Actions</span></Th>
                </Tr>
              </THead>
              <TBody>
                {(state.history.length ? state.history : state.active).map((g) => (
                  <Tr key={g.id}>
                    <Td><StatusPill status={STATUS[g.status].state} size="sm">{STATUS[g.status].label}</StatusPill></Td>
                    <Td className="max-w-[320px] truncate" title={g.reason}>{g.reason}</Td>
                    <Td className="capitalize">{g.role}</Td>
                    <Td muted className="num text-meta max-sm:hidden">
                      {formatDateTime(g.created_at)}{g.granted_by ? ` · ${g.granted_by}` : ''}
                    </Td>
                    <Td muted className="num text-meta">
                      {g.status === 'revoked' ? `ended ${formatDateTime(g.revoked_at)}` : formatDateTime(g.expires_at)}
                    </Td>
                    <Td className="text-right">
                      {g.status === 'active' && state.can_manage && (
                        <Button size="sm" variant="danger" onClick={() => revoke(g)}>End access</Button>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableContainer>
        )}
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
