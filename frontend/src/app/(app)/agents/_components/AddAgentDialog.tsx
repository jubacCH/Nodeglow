'use client';

import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Loader2, Monitor, Terminal } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Field, Select } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { SegmentedControl } from '@/components/ui/Tabs';
import { CopyButton } from '@/components/ui/CopyButton';
import { post, apiErrorMessage } from '@/lib/api';
import type { Agent } from '@/types';

/** Response of POST /api/agents/install-tokens. The token is shown once. */
export interface InstallToken {
  id: number;
  token: string;
  expires_at: string | null;
  install_linux: string;
  install_windows: string;
}

const VALIDITY = [
  { hours: 24, label: '24 hours' },
  { hours: 24 * 7, label: '7 days' },
  { hours: 24 * 30, label: '30 days' },
] as const;

/** Agents that enrolled after the command was issued. */
export function newAgentsSince(agents: Agent[] | undefined, knownIds: ReadonlySet<number>): Agent[] {
  return (agents ?? []).filter((a) => !knownIds.has(a.id));
}

interface AddAgentDialogProps {
  open: boolean;
  onClose: () => void;
  /** The live agent list, so the dialog can say when the new agent reports in. */
  agents: Agent[] | undefined;
}

/**
 * Issues a per-install token and shows the finished install command. The
 * server never hands out a shared enrollment secret, so there is nothing to
 * show before the token exists.
 */
export function AddAgentDialog({ open, onClose, agents }: AddAgentDialogProps) {
  const [platform, setPlatform] = useState<'linux' | 'windows'>('linux');
  const [ttlHours, setTtlHours] = useState<number>(VALIDITY[0].hours);
  const [issued, setIssued] = useState<InstallToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const knownIds = useRef<Set<number>>(new Set());

  // A closed dialog forgets its token: it cannot be shown again anyway.
  useEffect(() => {
    if (!open) {
      setIssued(null);
      setError(null);
      setBusy(false);
    }
  }, [open]);

  async function issue() {
    setBusy(true);
    setError(null);
    try {
      knownIds.current = new Set((agents ?? []).map((a) => a.id));
      setIssued(await post<InstallToken>('/api/agents/install-tokens', { ttl_hours: ttlHours }));
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create the install command'));
    } finally {
      setBusy(false);
    }
  }

  const command = issued ? (platform === 'linux' ? issued.install_linux : issued.install_windows) : '';
  const connected = issued ? newAgentsSince(agents, knownIds.current) : [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title="Add agent"
      description="Create an install command and run it on the machine you want to monitor. The agent installs itself and reports to this server."
      footer={<Button variant="secondary" onClick={onClose}>{issued ? 'Done' : 'Cancel'}</Button>}
    >
      {!issued ? (
        <div className="space-y-4">
          <Field
            label="Command valid for"
            hint="The command can be used on several machines until it expires."
          >
            <Select value={String(ttlHours)} onChange={(e) => setTtlHours(Number(e.target.value))}>
              {VALIDITY.map((v) => (
                <option key={v.hours} value={v.hours}>{v.label}</option>
              ))}
            </Select>
          </Field>
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
              className="overflow-x-auto whitespace-pre-wrap break-all rounded-ctl border border-border bg-surface-2 p-4 pr-10 font-mono text-meta text-fg"
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
        </div>
      )}
    </Modal>
  );
}
