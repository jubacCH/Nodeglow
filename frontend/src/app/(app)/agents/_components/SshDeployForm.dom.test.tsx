// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SshDeployForm } from './SshDeployForm';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const calls: { url: string; body: unknown }[] = [];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

beforeEach(() => {
  calls.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, body });
    if (url.endsWith('/api/credentials/list')) return json(200, { credentials: [] });
    if (url.endsWith('/api/agent-deploy') && !body.host_key_fingerprint) {
      return json(409, { code: 'host_key_unknown', fingerprint: 'SHA256:abc', key_type: 'ssh-ed25519', host: 'web01', port: 22 });
    }
    if (url.endsWith('/api/agent-deploy')) {
      return json(202, { run_id: 'r1', jobs: [{ created_at: '2026-11-18T10:00:00Z' }] });
    }
    if (url.includes('/api/agent-deploy/r1')) {
      return json(200, { run_id: 'r1', done: true, jobs: [{
        id: 1, run_id: 'r1', kind: 'ssh', target: 'web01', agent_id: null, status: 'ok',
        detail: 'installed', created_at: null, started_at: null, finished_at: null,
      }] });
    }
    return json(404, {});
  }));
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

function type(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setValue.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

describe('SshDeployForm', () => {
  it('asks to trust the host key, then follows the run', async () => {
    const started = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <SshDeployForm onStarted={started} />
        </QueryClientProvider>,
      );
    });
    const inputs = container.querySelectorAll('input');
    act(() => {
      type(inputs[0] as HTMLInputElement, 'web01');
      type(container.querySelector('input[type="password"]') as HTMLInputElement, 'pw');
    });
    await act(async () => {
      (container.querySelector('button[type="submit"]') as HTMLButtonElement).click();
    });
    await flush();

    expect(container.textContent).toContain('SHA256:abc');
    expect(started).not.toHaveBeenCalled();
    const first = calls.find((c) => c.url.endsWith('/api/agent-deploy'))!;
    expect(first.body).toEqual({ host: 'web01', port: 22, username: 'root', password: 'pw' });

    const trust = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Trust and install')!;
    await act(async () => { trust.click(); });
    await flush();

    const confirmed = calls.filter((c) => c.url.endsWith('/api/agent-deploy'))[1];
    expect(confirmed.body).toMatchObject({ host_key_fingerprint: 'SHA256:abc' });
    expect(started).toHaveBeenCalledWith('2026-11-18T10:00:00Z');
    expect(container.textContent).toContain('Installed.');
    expect(container.textContent).toContain('web01');
  });
});
