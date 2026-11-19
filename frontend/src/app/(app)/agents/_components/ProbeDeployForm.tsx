'use client';

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select } from '@/components/ui/Field';
import { SegmentedControl } from '@/components/ui/Tabs';
import { DeployRunStatus } from '@/components/agents/DeployRunStatus';
import { HostKeyPrompt } from '@/components/agents/HostKeyPrompt';
import { apiErrorBody, apiErrorMessage, get, post } from '@/lib/api';
import type { HostKeyQuestion } from '@/lib/agentDeploy';
import {
  EMPTY_PROBE_FORM, deployProbes, probeDeployBody, probeFormProblem, type ProbeForm,
} from '@/lib/probeDeploy';
import type { Agent } from '@/types';

interface CommandView {
  status: 'queued' | 'delivered' | 'ok' | 'failed' | 'expired';
  result: { fingerprint?: string; key_type?: string; detail?: string } | null;
}

const POLL_MS = 2000;
const ASK_TIMEOUT_MS = 3 * 60 * 1000;

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
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState<HostKeyQuestion | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const probe = probes.find((p) => String(p.id) === form.probeId);
  const set = (key: keyof ProbeForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  /** Ask the probe which host key the target presents, and wait for it. */
  async function askHostKey() {
    setAsking(true);
    try {
      const port = form.port.trim() === '' ? 22 : Number(form.port);
      const host = form.host.trim();
      const { command_id } = await post<{ command_id: number }>(
        `/api/probes/${form.probeId}/host-key`, { host, port });
      const deadline = Date.now() + ASK_TIMEOUT_MS;
      while (alive.current && Date.now() < deadline) {
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
      if (alive.current) setError('The probe did not answer. Is it online and up to date?');
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
      <p role="status" className="flex items-center gap-2 text-meta text-fg-2">
        <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
        Asking the probe for the host key of {form.host.trim()}. It answers with its next heartbeat.
      </p>
    );
  }

  if (probes.length === 0) {
    return (
      <p className="text-meta text-fg-2">
        No probe may install agents yet. Allow it on the probe with <span className="font-code">/opt/nodeglow/deploy.json</span>{' '}
        (see the operations guide, “Deploying agents from a probe”).
      </p>
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
      <Field label="Credential on the probe" required hint="Defined in the probe's deploy.json; only its name is known here.">
        <Select value={form.credential} onChange={set('credential')} disabled={!probe}>
          <option value="">Pick a credential</option>
          {(probe?.deploy?.credentials ?? []).map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </Select>
      </Field>
      {error && <p role="alert" className="text-meta text-down">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy}>
        {busy ? 'Starting…' : agentId !== undefined ? 'Reinstall through the probe' : 'Install through the probe'}
      </Button>
    </form>
  );
}
