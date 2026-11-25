import { describe, expect, it } from 'vitest';
import { loginFormProblem, parseHosts, type LoginForm } from './probeCredentials';

const form = (patch: Partial<LoginForm> = {}): LoginForm => ({
  name: 'lab', username: 'root', auth: 'password', password: 'pw', privateKey: '', passphrase: '',
  hosts: '10.0.0.5', ...patch,
});

describe('parseHosts', () => {
  it('splits on commas and whitespace and drops duplicates', () => {
    expect(parseHosts('10.0.0.0/24, a.lab  a.lab')).toEqual(['10.0.0.0/24', 'a.lab']);
    expect(parseHosts('  ')).toEqual([]);
  });
});

describe('loginFormProblem', () => {
  it('accepts a complete password login', () => {
    expect(loginFormProblem(form(), [], [])).toBeNull();
  });
  it('accepts a complete key login', () => {
    expect(loginFormProblem(form({ auth: 'key', password: '', privateKey: 'KEY' }), [], [])).toBeNull();
  });
  it('asks for the missing secret', () => {
    expect(loginFormProblem(form({ password: '' }), [], [])).toMatch(/password/i);
    expect(loginFormProblem(form({ auth: 'key', password: '', privateKey: ' ' }), [], [])).toMatch(/key/i);
  });
  it('refuses both secrets at once', () => {
    expect(loginFormProblem(form({ auth: 'password', privateKey: 'KEY' }), [], [])).toMatch(/only one/i);
    expect(loginFormProblem(form({ auth: 'key', password: 'pw', privateKey: 'KEY' }), [], [])).toMatch(/only one/i);
  });
  it('needs a username and at least one host', () => {
    expect(loginFormProblem(form({ username: ' ' }), [], [])).toMatch(/username/i);
    expect(loginFormProblem(form({ hosts: ' , ' }), [], [])).toMatch(/host/i);
  });
  it('refuses a taken or invalid name', () => {
    expect(loginFormProblem(form(), ['lab'], [])).toMatch(/already/i);
    expect(loginFormProblem(form(), [], ['lab'])).toMatch(/already/i);
    expect(loginFormProblem(form({ name: 'bad name' }), [], [])).toMatch(/letters/i);
    expect(loginFormProblem(form({ name: '' }), [], [])).toMatch(/name/i);
  });
});
