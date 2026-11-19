// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '@/types';
import { ProbeDeployForm } from './ProbeDeployForm';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const calls: { url: string; method: string; body: unknown }[] = [];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const probe = { id: 4, name: 'probe1', is_probe: true,
  deploy: { enabled: true, credentials: ['lab-root'], arch: ['x86_64'] } } as unknown as Agent;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method: init?.method ?? 'GET', body });
    if (url.endsWith('/api/probes/4/deploy') && !body.host_key_fingerprint) {
      return json(409, { code: 'host_key_unknown', host: 'web01', port: 22 });
    }
    if (url.endsWith('/api/probes/4/deploy')) return json(202, { run_id: 'r9', jobs: [{ created_at: '2026-11-19T10:00:00Z' }] });
    if (url.endsWith('/api/probes/4/host-key')) return json(202, { command_id: 11 });
    if (url.endsWith('/api/probes/4/commands/11')) {
      return json(200, { status: 'ok', result: { fingerprint: 'SHA256:probe', key_type: 'ssh-ed25519' } });
    }
    if (url.includes('/api/agent-deploy/r9')) {
      return json(200, { run_id: 'r9', done: false, jobs: [{ id: 1, run_id: 'r9', kind: 'probe_host', target: 'web01',
        agent_id: null, status: 'queued', detail: null, created_at: null, started_at: null, finished_at: null }] });
    }
    return json(404, {});
  }));
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function settle(ms = 0) {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  }
}

describe('ProbeDeployForm', () => {
  it('asks the probe for the host key, then deploys with the confirmed one', async () => {
    const started = vi.fn();
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <ProbeDeployForm agents={[probe]} onStarted={started} />
        </QueryClientProvider>,
      );
    });
    // One probe with one credential: both are preselected.
    const host = container.querySelector('input') as HTMLInputElement;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(host, 'web01');
      host.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => { (container.querySelector('button[type="submit"]') as HTMLButtonElement).click(); });
    await settle(2500);

    expect(calls.find((c) => c.url.endsWith('/host-key'))?.body).toEqual({ host: 'web01', port: 22 });
    expect(container.textContent).toContain('SHA256:probe');
    expect(container.textContent).toContain('The probe has not connected');

    const trust = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Trust and install')!;
    await act(async () => { trust.click(); });
    await settle();

    const deploys = calls.filter((c) => c.url.endsWith('/api/probes/4/deploy'));
    expect(deploys[1].body).toEqual({ target: { kind: 'host', host: 'web01', port: 22 }, credential: 'lab-root',
      host_key_fingerprint: 'SHA256:probe' });
    expect(started).toHaveBeenCalledWith('2026-11-19T10:00:00Z');
    expect(container.textContent).toContain('next heartbeat');
  });
});
