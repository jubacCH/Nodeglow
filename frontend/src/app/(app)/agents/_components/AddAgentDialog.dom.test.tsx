// @vitest-environment jsdom
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '@/types';
import { AddAgentDialog, issueMoment, type ReinstallTarget } from './AddAgentDialog';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
// One client for the whole test: a re-render is a poll, not a new app.
let client: QueryClient;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } });
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

function render(target: ReinstallTarget) {
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <AddAgentDialog open onClose={() => {}} agents={[target as Agent]} agent={target} />
      </QueryClientProvider>,
    );
  });
}

describe('AddAgentDialog reinstall mode in the browser', () => {
  it('keeps the chosen validity when the agent is polled again', () => {
    render({ id: 7, name: 'web01', platform: 'Linux', last_seen: '2026-11-16T12:00:00Z' });
    const select = document.body.querySelector('select') as HTMLSelectElement;
    expect(select.value).toBe('1');

    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
      setValue.call(select, '24');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(select.value).toBe('24');

    // The next poll hands the dialog a new object for the same agent.
    render({ id: 7, name: 'web01', platform: 'Linux', last_seen: '2026-11-16T12:00:15Z' });
    expect((document.body.querySelector('select') as HTMLSelectElement).value).toBe('24');
  });
});

describe('AddAgentDialog probe option on a reinstall', () => {
  const probe = { id: 4, name: 'probe1', is_probe: true,
    deploy: { enabled: true, credentials: ['lab-root'], arch: ['x86_64'] } } as unknown as Agent;

  afterEach(() => vi.unstubAllGlobals());

  it('offers "Through a probe" although the caller passed only the reinstalled agent', async () => {
    const target = { id: 7, name: 'web01', platform: 'Linux', last_seen: '2026-11-16T12:00:00Z' };
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/api/v1/agents')) return json([target, probe]);
      if (url.endsWith('/api/enrollment-info')) return json({ ssh_deploy: false });
      return json({});
    }));
    // Queries run here, unlike in the tests above.
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(target);
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    const labels = Array.from(document.body.querySelectorAll('button')).map((b) => b.textContent);
    expect(labels).toContain('Through a probe');
  });
});

describe('AddAgentDialog probe checkbox', () => {
  it('says the installer switches deploying on', () => {
    render({ id: 7, name: 'web01', platform: 'Linux', last_seen: '2026-11-16T12:00:00Z' });
    expect(document.body.textContent).toContain('The installer switches deploying on');
    expect(document.body.textContent).not.toContain('still needs its own go-ahead');
  });
});

describe('issueMoment', () => {
  it('prefers the server time of the issued command over the browser clock', () => {
    expect(issueMoment({ issued_at: '2026-11-16T12:00:00Z' }, 99)).toBe(Date.parse('2026-11-16T12:00:00Z'));
    expect(issueMoment({}, 99)).toBe(99);
    expect(issueMoment({ issued_at: null }, 99)).toBe(99);
  });
});
