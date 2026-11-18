'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select, Textarea } from '@/components/ui/Field';
import { DeployRunStatus } from '@/components/agents/DeployRunStatus';
import { HostKeyPrompt } from '@/components/agents/HostKeyPrompt';
import { apiErrorMessage, get, post } from '@/lib/api';
import {
  EMPTY_SSH_FORM, deployBody, formProblem, unknownHostKey, type HostKeyQuestion, type SshForm,
} from '@/lib/agentDeploy';

interface StoredCredential {
  id: number;
  name: string;
  type: string;
}

/**
 * Nodeglow installs the agent itself: it logs in over SSH and runs the
 * installer as root. Credentials are used for this one run and not stored
 * (unless they come from the credential store).
 */
export function SshDeployForm({
  agentId, onStarted,
}: {
  /** Reinstall this agent (the token is bound to it). */
  agentId?: number;
  /** Called with the server's time of the start (for the "connected" check). */
  onStarted: (serverTime: string | null) => void;
}) {
  const [form, setForm] = useState<SshForm>(EMPTY_SSH_FORM);
  const [question, setQuestion] = useState<HostKeyQuestion | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const credentials = useQuery({
    queryKey: ['credentials'],
    queryFn: async () => {
      const res = await get<{ credentials: StoredCredential[] } | StoredCredential[]>('/api/credentials/list');
      return Array.isArray(res) ? res : res.credentials ?? [];
    },
  });
  const sshCredentials = (credentials.data ?? []).filter((c) => c.type === 'ssh');

  const set = (key: keyof SshForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function start(fingerprint?: string) {
    const problem = formProblem(form);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ run_id: string; jobs?: { created_at: string | null }[] }>(
        '/api/agent-deploy', deployBody(form, { agentId, fingerprint }));
      setQuestion(null);
      // Started: the secrets are not needed in this browser any more.
      setForm((f) => ({ ...f, password: '', privateKey: '', passphrase: '', sudoPassword: '' }));
      setRunId(res.run_id);
      onStarted(res.jobs?.[0]?.created_at ?? null);
    } catch (e) {
      const asked = unknownHostKey(e);
      if (asked && !fingerprint) {
        setQuestion(asked);
      } else {
        setQuestion(null);
        setError(apiErrorMessage(e, 'Could not start the installation'));
      }
    } finally {
      setBusy(false);
    }
  }

  if (runId) return <DeployRunStatus runId={runId} />;

  if (question) {
    return (
      <HostKeyPrompt
        question={question}
        busy={busy}
        onTrust={() => start(question.fingerprint)}
        onCancel={() => setQuestion(null)}
      />
    );
  }

  const once = form.credentialId === '';
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        start();
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_6rem]">
        <Field label="Host" required>
          <Input value={form.host} onChange={set('host')} placeholder="web01.example.com or 10.0.0.5" autoComplete="off" />
        </Field>
        <Field label="Port">
          <Input value={form.port} onChange={set('port')} inputMode="numeric" />
        </Field>
      </div>
      <Field label="Credentials" hint={once ? 'Used for this installation only, not stored.' : undefined}>
        <Select value={form.credentialId} onChange={set('credentialId')}>
          <option value="">Enter for this installation</option>
          {sshCredentials.map((c) => (
            <option key={c.id} value={String(c.id)}>{c.name}</option>
          ))}
        </Select>
      </Field>
      {once && (
        <>
          <Field label="User name" required>
            <Input value={form.username} onChange={set('username')} autoComplete="off" />
          </Field>
          <Field label="Password" hint="Leave empty when you use a private key.">
            <Input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
          </Field>
          <Field label="Private key">
            <Textarea
              rows={3}
              value={form.privateKey}
              onChange={set('privateKey')}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              className="font-code"
            />
          </Field>
          {form.privateKey.trim() && (
            <Field label="Key passphrase">
              <Input type="password" value={form.passphrase} onChange={set('passphrase')} autoComplete="new-password" />
            </Field>
          )}
        </>
      )}
      <Field
        label="sudo password"
        hint="Only for a user other than root whose sudo asks for a password. It is passed to sudo alone."
      >
        <Input type="password" value={form.sudoPassword} onChange={set('sudoPassword')} autoComplete="new-password" />
      </Field>
      {error && <p role="alert" className="text-meta text-down">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? 'Connecting…' : agentId !== undefined ? 'Reinstall over SSH' : 'Install over SSH'}
      </Button>
    </form>
  );
}
