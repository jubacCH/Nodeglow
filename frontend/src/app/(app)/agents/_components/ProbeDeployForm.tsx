'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select } from '@/components/ui/Field';
import { SegmentedControl } from '@/components/ui/Tabs';
import { DeployRunStatus } from '@/components/agents/DeployRunStatus';
import { HostKeyPrompt } from '@/components/agents/HostKeyPrompt';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorBody, apiErrorMessage, del, get, post } from '@/lib/api';
import type { ProbeCredential } from '@/lib/probeCredentials';
import { useToastStore } from '@/stores/toast';
import { SealedLoginForm } from './SealedLoginForm';
import type { HostKeyQuestion } from '@/lib/agentDeploy';
import {
  EMPTY_PROBE_FORM, deployProbes, hostKeyBody, probeDeployBody, probeFormProblem, sealedValue, type ProbeForm,
} from '@/lib/probeDeploy';
import type { Agent } from '@/types';

interface CommandView {
  status: 'queued' | 'delivered' | 'ok' | 'failed' | 'expired';
  result: { fingerprint?: string; key_type?: string; detail?: string } | null;
}

const POLL_MS = 2000;
const ASK_TIMEOUT_MS = 3 * 60 * 1000;
const ADD_LOGIN = '__add__';

/**
 * A probe installs the agent: on a host in its network, or in containers of
 * a Proxmox node. The login stays on the probe (its deploy.json); here the
 * admin names it.
 */
export function ProbeDeployForm({
  agents, agentId, onStarted,
}: {
  agents: Agent[] | undefined;
  /** Reinstall this agent (the token is bound to it). */
  agentId?: number;
  onStarted: (serverTime: string | null) => void;
}) {
  const probes = deployProbes(agents);
  const [form, setForm] = useState<ProbeForm>(() => ({
    ...EMPTY_PROBE_FORM,
    probeId: probes.length === 1 ? String(probes[0].id) : '',
    credential: probes.length === 1 && probes[0].deploy?.credentials.length === 1
      ? probes[0].deploy.credentials[0] : '',
  }));
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [adding, setAdding] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState<HostKeyQuestion | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  // Set on mount too: StrictMode mounts twice in development.
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const cancelled = useRef(false);

  const probe = probes.find((p) => String(p.id) === form.probeId);
  const sealedOn = Boolean(probe?.deploy?.sealed);
  const seal = probe?.deploy?.seal;
  const credsKey = ['probe-credentials', form.probeId];
  const saved = useQuery({
    queryKey: credsKey,
    queryFn: () => get<{ credentials: ProbeCredential[] }>(`/api/probes/${form.probeId}/credentials`),
    enabled: form.probeId !== '',
  });
  const savedLogins = saved.data?.credentials ?? [];
  const localLogins = probe?.deploy?.credentials ?? [];

  function pickLogin(value: string) {
    if (value === ADD_LOGIN) {
      setAdding(true);
      return;
    }
    setForm((f) => ({ ...f, credential: value }));
  }

  async function removeLogin(c: ProbeCredential) {
    const ok = await confirm({
      title: 'Delete this login?',
      description: `${c.name} is removed from Nodeglow. The probe can no longer use it. This cannot be undone.`,
      confirmLabel: 'Delete login',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/probes/${form.probeId}/credentials/${c.id}`);
      if (form.credential === sealedValue(c.id)) setForm((f) => ({ ...f, credential: '' }));
      await qc.invalidateQueries({ queryKey: credsKey });
      toast(`${c.name} deleted`, 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not delete the login'), 'error');
    }
  }

  async function trustNewKey() {
    if (!seal?.reported_fingerprint) return;
    setKeyBusy(true);
    try {
      await post(`/api/probes/${form.probeId}/seal-key/trust`, { fingerprint: seal.reported_fingerprint });
      await qc.invalidateQueries({ queryKey: ['agents'] });
      await qc.invalidateQueries({ queryKey: credsKey });
      toast('New probe key trusted', 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not trust the new key'), 'error');
    } finally {
      if (alive.current) setKeyBusy(false);
    }
  }

  async function revokeAll() {
    const ok = await confirm({
      title: 'Revoke all sealed logins?',
      description: 'The probe makes a new sealing key and every login saved in Nodeglow stops working. You enter them again afterwards. Logins kept on the probe are not affected.',
      confirmLabel: 'Revoke all',
      variant: 'danger',
    });
    if (!ok) return;
    setKeyBusy(true);
    try {
      const { command_id } = await post<{ command_id: number }>(`/api/probes/${form.probeId}/seal-key/rotate`);
      toast('The probe replaces its key with its next heartbeat. Logins saved for the old key must be entered again.', 'success');
      const deadline = Date.now() + ASK_TIMEOUT_MS;
      while (alive.current && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const cmd = await get<CommandView>(`/api/probes/${form.probeId}/commands/${command_id}`);
        if (cmd.status === 'ok' || cmd.status === 'failed' || cmd.status === 'expired') break;
      }
      await qc.invalidateQueries({ queryKey: ['agents'] });
      await qc.invalidateQueries({ queryKey: credsKey });
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not revoke the sealed logins'), 'error');
    } finally {
      if (alive.current) setKeyBusy(false);
    }
  }

  const set = (key: keyof ProbeForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  /** Ask the probe which host key the target presents, and wait for it. */
  async function askHostKey() {
    cancelled.current = false;
    setAsking(true);
    try {
      const body = hostKeyBody(form);
      const host = body.host as string;
      const port = body.port as number;
      const { command_id } = await post<{ command_id: number }>(
        `/api/probes/${form.probeId}/host-key`, body);
      const deadline = Date.now() + ASK_TIMEOUT_MS;
      while (alive.current && !cancelled.current && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const cmd = await get<CommandView>(`/api/probes/${form.probeId}/commands/${command_id}`);
        if (cmd.status === 'ok' && cmd.result?.fingerprint) {
          setQuestion({ fingerprint: cmd.result.fingerprint, keyType: cmd.result.key_type ?? '', host, port });
          return;
        }
        if (cmd.status === 'failed' || cmd.status === 'expired') {
          setError(cmd.result?.detail ?? 'The probe could not read the host key.');
          return;
        }
      }
      if (alive.current && !cancelled.current) setError('The probe did not answer. Is it online and up to date?');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not ask the probe for the host key'));
    } finally {
      if (alive.current) setAsking(false);
    }
  }

  async function start(fingerprint?: string) {
    const problem = probeFormProblem(form, { reinstall: agentId !== undefined });
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ run_id: string; jobs?: { created_at: string | null }[] }>(
        `/api/probes/${form.probeId}/deploy`, probeDeployBody(form, { agentId, fingerprint }));
      setQuestion(null);
      setRunId(res.run_id);
      onStarted(res.jobs?.[0]?.created_at ?? null);
    } catch (e) {
      if (apiErrorBody(e)?.code === 'host_key_unknown' && !fingerprint) {
        setBusy(false);
        await askHostKey();
        return;
      }
      setQuestion(null);
      setError(apiErrorMessage(e, 'Could not start the installation'));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  if (runId) {
    return (
      <div className="space-y-2">
        <p className="text-meta text-fg-3">The probe picks this up with its next heartbeat, usually within a minute.</p>
        <DeployRunStatus runId={runId} />
      </div>
    );
  }

  if (question) {
    return (
      <HostKeyPrompt
        question={question}
        via="The probe"
        busy={busy}
        onTrust={() => start(question.fingerprint)}
        onCancel={() => setQuestion(null)}
      />
    );
  }

  if (asking) {
    return (
      <div className="space-y-3">
        <p role="status" className="flex items-center gap-2 text-meta text-fg-2">
          <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          Asking the probe for the host key of {form.host.trim()}. It answers with its next heartbeat.
        </p>
        <Button size="sm" onClick={() => { cancelled.current = true; setAsking(false); }}>Cancel</Button>
      </div>
    );
  }

  if (probes.length === 0) {
    return (
      <p className="text-meta text-fg-2">
        No probe may install agents yet. Tick “This machine is a probe and may install agents on other hosts” in the install
        command, or run <span className="font-code">sudo /opt/nodeglow/nodeglow-agent enable-deploy</span> on an existing probe
        (see the operations guide, “Deploying agents from a probe”).
      </p>
    );
  }

  if (adding && probe) {
    return (
      <div className="space-y-3">
        <p className="text-ui font-medium text-fg">Add a login for {probe.name || probe.hostname}</p>
        <SealedLoginForm
          probe={probe}
          defaultHost={form.host}
          takenNames={[...localLogins, ...savedLogins.map((c) => c.name)]}
          onSaved={(c) => {
            setForm((f) => ({ ...f, credential: sealedValue(c.id) }));
            setAdding(false);
            void qc.invalidateQueries({ queryKey: credsKey });
          }}
          onCancel={() => setAdding(false)}
        />
      </div>
    );
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        start();
      }}
    >
      <Field label="Probe" required>
        <Select
          value={form.probeId}
          onChange={(e) => setForm((f) => ({ ...f, probeId: e.target.value, credential: '' }))}
        >
          <option value="">Pick a probe</option>
          {probes.map((p) => (
            <option key={p.id} value={String(p.id)}>{p.name || p.hostname}</option>
          ))}
        </Select>
      </Field>
      <SegmentedControl
        label="Install on"
        value={form.kind}
        onChange={(kind) => setForm((f) => ({ ...f, kind }))}
        options={[
          { value: 'host', label: 'A host' },
          { value: 'lxc', label: 'Proxmox containers' },
        ]}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_6rem]">
        <Field label={form.kind === 'lxc' ? 'Proxmox node' : 'Host'} required>
          <Input value={form.host} onChange={set('host')} placeholder="10.0.0.5 or pve1.example.com" autoComplete="off" />
        </Field>
        <Field label="SSH port">
          <Input value={form.port} onChange={set('port')} inputMode="numeric" />
        </Field>
      </div>
      {form.kind === 'lxc' && (
        <Field label="Container ids" required hint="For example 105, 107 or 110-112.">
          <Input value={form.vmids} onChange={set('vmids')} placeholder="105, 107" autoComplete="off" />
        </Field>
      )}
      {seal?.state === 'changed' && (
        <div role="alert" className="space-y-2 rounded-ctl border border-down/40 bg-down-soft px-3 py-2 text-meta text-fg">
          <p className="font-medium">The probe reports a different sealing key.</p>
          <p>Trusted: <span className="break-all font-code">{seal.fingerprint}</span></p>
          <p>Reported now: <span className="break-all font-code">{seal.reported_fingerprint}</span></p>
          <p className="text-fg-2">
            Compare it with <span className="font-code">sudo /opt/nodeglow/nodeglow-agent seal-key</span> on the probe.
            Logins sealed to the old key stop working.
          </p>
          <Button size="sm" onClick={trustNewKey} disabled={keyBusy}>Trust the new key</Button>
        </div>
      )}
      <Field label="Login" required hint="Logins on the probe come from its deploy.json; only the name is known here.">
        <Select value={form.credential} onChange={(e) => pickLogin(e.target.value)} disabled={!probe}>
          <option value="">Pick a login</option>
          {localLogins.length > 0 && (
            <optgroup label="On the probe">
              {localLogins.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </optgroup>
          )}
          {savedLogins.length > 0 && (
            <optgroup label="Saved in Nodeglow">
              {savedLogins.map((c) => (
                <option key={c.id} value={sealedValue(c.id)} disabled={c.stale || !sealedOn}>
                  {c.name} ({c.username}){c.stale ? ' - sealed to an old key' : !sealedOn ? ' - switched off' : ''}
                </option>
              ))}
            </optgroup>
          )}
          {sealedOn && seal?.state === 'trusted' && <option value={ADD_LOGIN}>Add a login…</option>}
        </Select>
      </Field>
      {saved.isError && (
        <p role="alert" className="text-meta text-down">Could not load the logins saved in Nodeglow.</p>
      )}
      {!sealedOn && savedLogins.length > 0 && (
        <p className="text-meta text-fg-3">Sealed logins are switched off on this probe.</p>
      )}
      {savedLogins.length > 0 && (
        <ul className="divide-y divide-border rounded-ctl border border-border text-meta" aria-label="Logins saved in Nodeglow">
          {savedLogins.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-fg">
                  {c.name} <span className="text-fg-3">({c.username}, {c.auth === 'key' ? 'key' : 'password'})</span>
                  {c.stale && <span className="ml-2 text-down">sealed to an old key</span>}
                </p>
                <p className="truncate text-fg-3">{c.hosts.join(', ')}</p>
              </div>
              <Button size="sm" variant="ghost" aria-label={`Delete ${c.name}`} onClick={() => removeLogin(c)}>
                <Trash2 size={13} aria-hidden="true" /> Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
      {sealedOn && !seal?.fingerprint && (
        <p className="text-meta text-fg-3">The probe has not reported a sealing key yet, so logins cannot be saved here.</p>
      )}
      {sealedOn && seal?.state !== 'none' && (
        <div>
          <Button size="sm" variant="ghost" onClick={revokeAll} disabled={keyBusy}>Revoke all sealed logins</Button>
        </div>
      )}
      {error && <p role="alert" className="text-meta text-down">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? 'Starting…' : agentId !== undefined ? 'Reinstall through the probe' : 'Install through the probe'}
      </Button>
      {ConfirmDialogElement}
    </form>
  );
}
