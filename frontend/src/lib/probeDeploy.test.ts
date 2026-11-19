import { describe, expect, it } from 'vitest';
import type { Agent } from '@/types';
import { EMPTY_PROBE_FORM, deployProbes, parseVmids, probeDeployBody, probeFormProblem, type ProbeForm } from './probeDeploy';

const probe = (id: number, enabled: boolean, credentials = ['lab-root']) =>
  ({ id, name: `p${id}`, is_probe: true, deploy: { enabled, credentials, arch: ['x86_64'] } }) as unknown as Agent;

const form = (patch: Partial<ProbeForm> = {}): ProbeForm => ({
  ...EMPTY_PROBE_FORM, probeId: '1', host: 'web01', credential: 'lab-root', ...patch,
});

describe('deployProbes', () => {
  it('lists probes that switched deploying on', () => {
    const plain = { id: 9, name: 'a', is_probe: false } as unknown as Agent;
    expect(deployProbes([probe(1, true), probe(2, false), plain]).map((p) => p.id)).toEqual([1]);
    expect(deployProbes(undefined)).toEqual([]);
  });
});

describe('parseVmids', () => {
  it('reads ids and ranges', () => {
    expect(parseVmids('105, 107 110-112')).toEqual([105, 107, 110, 111, 112]);
    expect(parseVmids('105,105')).toEqual([105]);
  });
  it('rejects anything else', () => {
    expect(parseVmids('')).toBeNull();
    expect(parseVmids('105; rm')).toBeNull();
    expect(parseVmids('112-110')).toBeNull();
    expect(parseVmids('1-100')).toBeNull();
  });
});

describe('probeFormProblem', () => {
  it('accepts a complete form', () => {
    expect(probeFormProblem(form())).toBeNull();
    expect(probeFormProblem(form({ kind: 'lxc', vmids: '105' }))).toBeNull();
  });
  it.each([
    [{ probeId: '' }, 'probe'],
    [{ host: ' ' }, 'host'],
    [{ port: '0' }, 'port'],
    [{ credential: '' }, 'credential'],
    [{ kind: 'lxc' as const, vmids: 'x' }, 'container'],
  ])('names what is missing: %j', (patch, text) => {
    expect(probeFormProblem(form(patch))).toContain(text);
  });
  it('allows only one target for a reinstall', () => {
    expect(probeFormProblem(form({ kind: 'lxc', vmids: '1,2' }), { reinstall: true })).toContain('one');
  });
});

describe('probeDeployBody', () => {
  it('builds a host target', () => {
    expect(probeDeployBody(form({ port: '2222' }))).toEqual({
      target: { kind: 'host', host: 'web01', port: 2222 }, credential: 'lab-root',
    });
  });
  it('builds containers, the reinstall target and a confirmed key', () => {
    expect(probeDeployBody(form({ kind: 'lxc', host: 'pve1', vmids: '105 107' }),
      { agentId: 3, fingerprint: 'SHA256:x' })).toEqual({
      target: { kind: 'lxc', host: 'pve1', port: 22, vmids: [105, 107] }, credential: 'lab-root',
      agent_id: 3, host_key_fingerprint: 'SHA256:x',
    });
  });
});
