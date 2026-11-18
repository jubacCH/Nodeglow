import { apiErrorBody } from './api';

/** One target of a deploy run (GET /api/agent-deploy/{run_id}). */
export interface DeployJob {
  id: number;
  run_id: string;
  kind: 'ssh' | 'proxmox_lxc';
  target: string;
  agent_id: number | null;
  status: 'queued' | 'running' | 'ok' | 'failed';
  detail: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DeployRun {
  run_id: string;
  done: boolean;
  jobs: DeployJob[];
}

/** The SSH form. `credentialId` empty means "enter credentials once". */
export interface SshForm {
  host: string;
  port: string;
  credentialId: string;
  username: string;
  password: string;
  privateKey: string;
  passphrase: string;
  sudoPassword: string;
}

export const EMPTY_SSH_FORM: SshForm = {
  host: '', port: '22', credentialId: '', username: 'root', password: '',
  privateKey: '', passphrase: '', sudoPassword: '',
};

export interface HostKeyQuestion {
  fingerprint: string;
  keyType: string;
  host: string;
  port: number;
}

function portOf(form: SshForm): number {
  return form.port.trim() === '' ? 22 : Number(form.port);
}

/** What is still missing, in words, or null when the form can be sent. */
export function formProblem(form: SshForm): string | null {
  if (!form.host.trim()) return 'Enter the host to install on.';
  const port = portOf(form);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return 'The port must be between 1 and 65535.';
  if (form.credentialId) return null;
  if (!form.username.trim()) return 'Enter the user name to log in with.';
  if (!form.password && !form.privateKey.trim()) return 'Enter a password or a private key.';
  return null;
}

/** Body of POST /api/agent-deploy. Empty fields are left out. */
export function deployBody(
  form: SshForm,
  opts: { agentId?: number; fingerprint?: string } = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = { host: form.host.trim(), port: portOf(form) };
  if (form.credentialId) {
    body.credential_id = Number(form.credentialId);
  } else {
    body.username = form.username.trim();
    if (form.password) body.password = form.password;
    if (form.privateKey.trim()) body.private_key = form.privateKey;
    if (form.passphrase) body.passphrase = form.passphrase;
  }
  if (form.sudoPassword) body.sudo_password = form.sudoPassword;
  if (opts.agentId !== undefined) body.agent_id = opts.agentId;
  if (opts.fingerprint) body.host_key_fingerprint = opts.fingerprint;
  return body;
}

/** The host key to confirm, when the server asks for it (409 host_key_unknown). */
export function unknownHostKey(e: unknown): HostKeyQuestion | null {
  const body = apiErrorBody(e);
  if (body?.code !== 'host_key_unknown' || typeof body.fingerprint !== 'string') return null;
  return {
    fingerprint: body.fingerprint,
    keyType: String(body.key_type ?? ''),
    host: String(body.host ?? ''),
    port: Number(body.port ?? 22),
  };
}

export type RunOutcome = 'running' | 'ok' | 'failed' | 'partial';

export function runOutcome(run: DeployRun): RunOutcome {
  if (run.jobs.some((j) => j.status === 'queued' || j.status === 'running')) return 'running';
  const failed = run.jobs.filter((j) => j.status === 'failed').length;
  if (failed === 0) return 'ok';
  return failed === run.jobs.length ? 'failed' : 'partial';
}
