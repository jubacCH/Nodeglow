import type { Agent } from '@/types';

/** Containers a single run may name (the server allows 50). */
export const MAX_CONTAINERS = 50;

/** The "Install through a probe" form. */
export interface ProbeForm {
  probeId: string;
  kind: 'host' | 'lxc';
  host: string;
  port: string;
  vmids: string;
  credential: string;
}

export const EMPTY_PROBE_FORM: ProbeForm = {
  probeId: '', kind: 'host', host: '', port: '22', vmids: '', credential: '',
};

/** Probes whose local deploy.json switched deploying on. */
export function deployProbes(agents: Agent[] | undefined): Agent[] {
  return (agents ?? []).filter((a) => a.is_probe && a.deploy?.enabled);
}

/** "105, 107 110-112" → [105, 107, 110, 111, 112]; null when invalid. */
export function parseVmids(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  if (parts.length === 0) return null;
  const ids: number[] = [];
  for (const part of parts) {
    const m = /^(\d{1,9})(?:-(\d{1,9}))?$/.exec(part);
    if (!m) return null;
    const from = Number(m[1]);
    const to = m[2] ? Number(m[2]) : from;
    if (from < 1 || to < from || to - from >= MAX_CONTAINERS) return null;
    for (let id = from; id <= to; id++) if (!ids.includes(id)) ids.push(id);
  }
  return ids.length <= MAX_CONTAINERS ? ids : null;
}

function portOf(form: ProbeForm): number {
  return form.port.trim() === '' ? 22 : Number(form.port);
}

/** What is still missing, in words, or null when the form can be sent. */
export function probeFormProblem(form: ProbeForm, opts: { reinstall?: boolean } = {}): string | null {
  if (!form.probeId) return 'Pick the probe that should install.';
  if (!form.host.trim()) return form.kind === 'lxc' ? 'Enter the Proxmox node (host).' : 'Enter the host to install on.';
  const port = portOf(form);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return 'The port must be between 1 and 65535.';
  if (!form.credential) return 'Pick a credential of the probe.';
  if (form.kind === 'lxc') {
    const ids = parseVmids(form.vmids);
    if (!ids) return `Enter container ids, e.g. 105, 107 or 110-112 (at most ${MAX_CONTAINERS}).`;
    if (opts.reinstall && ids.length !== 1) return 'A reinstall goes to one container.';
  }
  return null;
}

/** Body of POST /api/probes/{id}/deploy. */
export function probeDeployBody(
  form: ProbeForm,
  opts: { agentId?: number; fingerprint?: string } = {},
): Record<string, unknown> {
  const host = form.host.trim();
  const port = portOf(form);
  const target = form.kind === 'lxc'
    ? { kind: 'lxc', host, port, vmids: parseVmids(form.vmids) ?? [] }
    : { kind: 'host', host, port };
  const body: Record<string, unknown> = { target, credential: form.credential };
  if (opts.agentId !== undefined) body.agent_id = opts.agentId;
  if (opts.fingerprint) body.host_key_fingerprint = opts.fingerprint;
  return body;
}
