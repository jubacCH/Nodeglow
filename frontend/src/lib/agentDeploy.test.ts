import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import {
  EMPTY_SSH_FORM, deployBody, formProblem, runOutcome, unknownHostKey, type DeployRun, type SshForm,
} from './agentDeploy';

const form = (patch: Partial<SshForm> = {}): SshForm => ({
  ...EMPTY_SSH_FORM, host: 'web01', username: 'root', password: 'pw', ...patch,
});

describe('deployBody', () => {
  it('sends credentials entered once, without empty fields', () => {
    expect(deployBody(form({ host: ' web01 ', port: '2222' }))).toEqual({
      host: 'web01', port: 2222, username: 'root', password: 'pw',
    });
  });

  it('sends only the id of a stored credential', () => {
    expect(deployBody(form({ credentialId: '7', password: 'ignored' }))).toEqual({
      host: 'web01', port: 22, credential_id: 7,
    });
  });

  it('adds the reinstall target, the sudo password and a confirmed key', () => {
    expect(deployBody(form({ sudoPassword: 's', privateKey: 'KEY', password: '' }),
      { agentId: 3, fingerprint: 'SHA256:x' })).toEqual({
      host: 'web01', port: 22, username: 'root', private_key: 'KEY', sudo_password: 's',
      agent_id: 3, host_key_fingerprint: 'SHA256:x',
    });
  });
});

describe('formProblem', () => {
  it('accepts a complete form', () => {
    expect(formProblem(form())).toBeNull();
    expect(formProblem(form({ username: '', password: '', credentialId: '2' }))).toBeNull();
  });

  it.each([
    [{ host: ' ' }, 'host'],
    [{ port: '0' }, 'port'],
    [{ port: '70000' }, 'port'],
    [{ username: '' }, 'user name'],
    [{ password: '', privateKey: '' }, 'password or a private key'],
  ])('names what is missing: %j', (patch, text) => {
    expect(formProblem(form(patch as Partial<SshForm>))).toContain(text);
  });
});

describe('unknownHostKey', () => {
  it('reads the fingerprint from a 409 host_key_unknown', () => {
    const e = new ApiError(409, 'conflict', JSON.stringify({
      code: 'host_key_unknown', fingerprint: 'SHA256:abc', key_type: 'ssh-ed25519', host: 'web01', port: 22,
    }));
    expect(unknownHostKey(e)).toEqual({ fingerprint: 'SHA256:abc', keyType: 'ssh-ed25519', host: 'web01', port: 22 });
  });

  it('ignores every other error', () => {
    expect(unknownHostKey(new ApiError(409, 'x', JSON.stringify({ code: 'agent_disabled' })))).toBeNull();
    expect(unknownHostKey(new Error('x'))).toBeNull();
  });
});

describe('runOutcome', () => {
  const run = (...statuses: string[]): DeployRun => ({
    run_id: 'r', done: statuses.every((s) => s === 'ok' || s === 'failed'),
    jobs: statuses.map((status, i) => ({
      id: i, run_id: 'r', kind: 'ssh', target: `t${i}`, agent_id: null,
      status: status as DeployRun['jobs'][number]['status'], detail: null,
      created_at: null, started_at: null, finished_at: null,
    })),
  });

  it('summarises a run', () => {
    expect(runOutcome(run('queued', 'ok'))).toBe('running');
    expect(runOutcome(run('ok', 'ok'))).toBe('ok');
    expect(runOutcome(run('failed'))).toBe('failed');
    expect(runOutcome(run('ok', 'failed'))).toBe('partial');
  });
});
