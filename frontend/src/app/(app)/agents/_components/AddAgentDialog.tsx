'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Loader2, Monitor, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Checkbox, Field, Select } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { SegmentedControl } from '@/components/ui/Tabs';
import { CopyButton } from '@/components/ui/CopyButton';
import { get, post, apiErrorMessage } from '@/lib/api';
import { SshDeployForm } from './SshDeployForm';
import { ProbeDeployForm } from './ProbeDeployForm';
import { deployProbes } from '@/lib/probeDeploy';
import { useAgents } from '@/hooks/queries/useAgents';
import type { Agent } from '@/types';

/** Response of POST /api/agents/install-tokens. The token is shown once. */
export interface InstallToken {
  id: number;
  token: string;
  expires_at: string | null;
  install_linux: string;
  install_windows: string;
  /** Reinstall only: when the server issued it (its clock, not the browser's). */
  issued_at?: string | null;
}

const VALIDITY = [
  { hours: 24, label: '24 hours' },
  { hours: 24 * 7, label: '7 days' },
  { hours: 24 * 30, label: '30 days' },
] as const;

/** A reinstall command works once, so it need not live long. */
const REINSTALL_VALIDITY = [
  { hours: 1, label: '1 hour' },
  { hours: 24, label: '24 hours' },
] as const;

/** The agent a reinstall command is for. */
export type ReinstallTarget = Pick<Agent, 'id' | 'name' | 'platform' | 'last_seen'>;

type Platform = 'linux' | 'windows';

export function validityFor(target: ReinstallTarget | undefined): ReadonlyArray<{ hours: number; label: string }> {
  return target ? REINSTALL_VALIDITY : VALIDITY;
}

/** Where to ask for the command: a bound token for a reinstall, a plain one otherwise. */
export function issueRequest(target: ReinstallTarget | undefined, ttlHours: number, probe = false) {
  return {
    url: target ? `/api/agents/${target.id}/reinstall` : '/api/agents/install-tokens',
    body: probe ? { ttl_hours: ttlHours, probe: true } : { ttl_hours: ttlHours },
  };
}

export function initialPlatform(target: ReinstallTarget | undefined): Platform {
  return target?.platform?.toLowerCase() === 'windows' ? 'windows' : 'linux';
}

/** When the command was issued, by the server's clock where it says so. */
export function issueMoment(token: Pick<InstallToken, 'issued_at'>, browserNow: number): number {
  const server = token.issued_at ? Date.parse(token.issued_at) : NaN;
  return Number.isNaN(server) ? browserNow : server;
}

/** Has the reinstalled agent reported since the command was issued? */
export function reconnectedSince(target: ReinstallTarget | undefined, issuedAt: number): boolean {
  if (!target?.last_seen) return false;
  return Date.parse(target.last_seen) > issuedAt;
}

/** Whether to offer "Install over SSH": the server allows it, and the
 * installer it runs is the Linux one. */
export function canDeployOverSsh(sshDeploy: boolean | undefined, target: ReinstallTarget | undefined): boolean {
  return sshDeploy === true && initialPlatform(target) === 'linux';
}

type Method = 'command' | 'ssh' | 'probe';

/** Agents that enrolled after the command was issued. */
export function newAgentsSince(agents: Agent[] | undefined, knownIds: ReadonlySet<number>): Agent[] {
  return (agents ?? []).filter((a) => !knownIds.has(a.id));
}

interface AddAgentDialogProps {
  open: boolean;
  onClose: () => void;
  /** The live agent list, so the dialog can say when the new agent reports in. */
  agents: Agent[] | undefined;
  /** Set to reinstall this existing agent instead of adding a new one. */
  agent?: ReinstallTarget;
}

/**
 * Issues a per-install token and shows the finished install command. The
 * server never hands out a shared enrollment secret, so there is nothing to
 * show before the token exists.
 */
export function AddAgentDialog({ open, onClose, agents: agentsProp, agent }: AddAgentDialogProps) {
  // The roster comes from the shared agents query, not from the caller: a
  // reinstall page only knows its own agent, yet the probes that can deploy
  // are other agents. The prop is the fallback until the query has data.
  const roster = useAgents({ enabled: open });
  const agents = roster.data ?? agentsProp;
  const validity = validityFor(agent);
  const [platform, setPlatform] = useState<Platform>(initialPlatform(agent));
  const [ttlHours, setTtlHours] = useState<number>(validity[0].hours);
  const [issued, setIssued] = useState<InstallToken | null>(null);
  const [probeOptIn, setProbeOptIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [method, setMethod] = useState<Method>('command');
  const [sshStarted, setSshStarted] = useState(false);
  const knownIds = useRef<Set<number>>(new Set());
  const issuedAt = useRef<number>(0);

  // A closed dialog forgets its token: it cannot be shown again anyway.
  useEffect(() => {
    if (!open) {
      setIssued(null);
      setProbeOptIn(false);
      setError(null);
      setBusy(false);
      setMethod('command');
      setSshStarted(false);
    }
  }, [open]);

  const info = useQuery({
    queryKey: ['enrollment-info'],
    queryFn: () => get<{ ssh_deploy?: boolean }>('/api/enrollment-info'),
    enabled: open,
    staleTime: 60_000,
  });
  const sshOffered = canDeployOverSsh(info.data?.ssh_deploy, agent);
  // Through a probe: for Linux targets, when a probe allows it locally.
  const probeOffered = initialPlatform(agent) === 'linux' && deployProbes(agents).length > 0;

  function sshRunStarted(serverTime: string | null) {
    knownIds.current = new Set((agents ?? []).map((a) => a.id));
    issuedAt.current = issueMoment({ issued_at: serverTime }, Date.now());
    setSshStarted(true);
  }

  // Defaults follow the agent, not every poll of it: a refetch hands in a new
  // object and must not undo what the admin picked.
  const agentId = agent?.id;
  const agentPlatform = agent?.platform;
  useEffect(() => {
    if (!open) return;
    const target = agentId === undefined ? undefined : { platform: agentPlatform ?? null } as ReinstallTarget;
    setPlatform(initialPlatform(target));
    setTtlHours(validityFor(target)[0].hours);
  }, [open, agentId, agentPlatform]);

  async function issue() {
    setBusy(true);
    setError(null);
    try {
      knownIds.current = new Set((agents ?? []).map((a) => a.id));
      const { url, body } = issueRequest(agent, ttlHours, probeOptIn && initialPlatform(agent) === 'linux');
      const token = await post<InstallToken>(url, body);
      issuedAt.current = issueMoment(token, Date.now());
      setIssued(token);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create the install command'));
    } finally {
      setBusy(false);
    }
  }

  const command = issued ? (platform === 'linux' ? issued.install_linux : issued.install_windows) : '';
  const live = agent ? (agents?.find((a) => a.id === agent.id) ?? agent) : undefined;
  const waiting = Boolean(issued) || sshStarted;
  const connected = !waiting
    ? []
    : agent
      ? (reconnectedSince(live, issuedAt.current) ? [live as Agent] : [])
      : newAgentsSince(agents, knownIds.current);

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={agent ? 'Reinstall agent' : 'Add agent'}
      description={agent
        ? `A new install command for ${agent.name}. Run it on that machine: the agent keeps its history and settings. The command works once and only on this host.`
        : 'Create an install command and run it on the machine you want to monitor. The agent installs itself and reports to this server.'}
      footer={<Button variant="secondary" onClick={onClose}>{waiting ? 'Done' : 'Cancel'}</Button>}
    >
      {(sshOffered || probeOffered) && !issued && !sshStarted && (
        <div className="mb-4">
          <SegmentedControl
            label="How to install"
            value={method}
            onChange={setMethod}
            options={[
              { value: 'command', label: 'Copy a command' },
              ...(sshOffered ? [{ value: 'ssh' as const, label: 'Install over SSH' }] : []),
              ...(probeOffered ? [{ value: 'probe' as const, label: 'Through a probe' }] : []),
            ]}
          />
        </div>
      )}
      {method === 'ssh' && sshOffered ? (
        <div className="space-y-4">
          <SshDeployForm agentId={agent?.id} onStarted={sshRunStarted} />
          {sshStarted && <ConnectionStatus connected={connected} />}
        </div>
      ) : method === 'probe' && probeOffered ? (
        <div className="space-y-4">
          <ProbeDeployForm agents={agents} agentId={agent?.id} onStarted={sshRunStarted} />
          {sshStarted && <ConnectionStatus connected={connected} />}
        </div>
      ) : !issued ? (
        <div className="space-y-4">
          <Field
            label="Command valid for"
            hint={agent
              ? 'The command can be used once, on this agent\'s machine.'
              : 'The command can be used on several machines until it expires.'}
          >
            <Select value={String(ttlHours)} onChange={(e) => setTtlHours(Number(e.target.value))}>
              {validity.map((v) => (
                <option key={v.hours} value={v.hours}>{v.label}</option>
              ))}
            </Select>
          </Field>
          {initialPlatform(agent) === 'linux' && (
            <Checkbox
              checked={probeOptIn}
              onChange={(e) => setProbeOptIn(e.target.checked)}
              label="This machine is a probe and may install agents on other hosts"
              description="Linux only. The installer switches deploying on on this machine (enable-deploy)."
            />
          )}
          {error && <p role="alert" className="text-meta text-down">{error}</p>}
          <Button onClick={issue} disabled={busy}>
            {busy ? 'Creating…' : 'Create install command'}
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <SegmentedControl
            label="Target platform"
            value={platform}
            onChange={setPlatform}
            options={[
              { value: 'linux', label: <><Terminal size={13} aria-hidden="true" /> Linux</> },
              { value: 'windows', label: <><Monitor size={13} aria-hidden="true" /> Windows</> },
            ]}
          />
          <div className="relative">
            <pre
              aria-label={`${platform === 'linux' ? 'Linux' : 'Windows'} install command`}
              className="overflow-x-auto whitespace-pre-wrap break-all rounded-ctl border border-border bg-surface-2 p-4 pr-10 font-code text-meta text-fg"
            >
              {command}
            </pre>
            <div className="absolute right-2 top-2">
              <CopyButton text={command} />
            </div>
          </div>
          <p className="text-meta text-fg-3">
            {platform === 'linux'
              ? 'Run it in a shell on the target machine.'
              : 'Run it in PowerShell as administrator on the target machine.'}{' '}
            The command contains a secret and is shown only now. Copy it before you close this dialog.
          </p>
          <ConnectionStatus connected={connected} />
        </div>
      )}
    </Modal>
  );
}

/** Says when the installed agent has reported in. */
function ConnectionStatus({ connected }: { connected: Agent[] }) {
  return (
    <div
      role="status"
      className="flex items-center gap-2 rounded-ctl border border-border bg-surface-2 px-3 py-2 text-meta"
    >
      {connected.length > 0 ? (
        <>
          <CheckCircle2 size={14} className="shrink-0 text-ok" aria-hidden="true" />
          <span className="text-fg">
            Connected: {connected.map((a) => a.name || a.hostname).join(', ')}
          </span>
        </>
      ) : (
        <>
          <Loader2 size={14} className="shrink-0 animate-spin text-fg-3 motion-reduce:animate-none" aria-hidden="true" />
          <span className="text-fg-2">Waiting for the agent to report in. This list updates by itself.</span>
        </>
      )}
    </div>
  );
}
