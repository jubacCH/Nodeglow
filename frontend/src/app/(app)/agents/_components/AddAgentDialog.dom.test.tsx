// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Agent } from '@/types';
import { AddAgentDialog, issueMoment, type ReinstallTarget } from './AddAgentDialog';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

function render(target: ReinstallTarget) {
  act(() => {
    root.render(
      <AddAgentDialog open onClose={() => {}} agents={[target as Agent]} agent={target} />,
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

describe('issueMoment', () => {
  it('prefers the server time of the issued command over the browser clock', () => {
    expect(issueMoment({ issued_at: '2026-11-16T12:00:00Z' }, 99)).toBe(Date.parse('2026-11-16T12:00:00Z'));
    expect(issueMoment({}, 99)).toBe(99);
    expect(issueMoment({ issued_at: null }, 99)).toBe(99);
  });
});
