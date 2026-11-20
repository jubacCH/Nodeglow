import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import type { Agent } from '@/types';
import {
  AddAgentDialog, canDeployOverSsh, initialPlatform, issueRequest, newAgentsSince, reconnectedSince, validityFor,
} from './AddAgentDialog';

/** The dialog asks the server whether SSH deploy is on. */
const withQuery = (el: ReactElement) => (
  <QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>
);

const agent = (id: number, name: string) => ({ id, name, hostname: name }) as Agent;

describe('AddAgentDialog', () => {
  it('renders before any token exists', () => {
    // The dialog used to read an enrollment key the server no longer sends
    // and crashed the whole page on open.
    expect(() => renderToStaticMarkup(withQuery(<AddAgentDialog open onClose={() => {}} agents={undefined} />))).not.toThrow();
    expect(() => renderToStaticMarkup(withQuery(<AddAgentDialog open onClose={() => {}} agents={[agent(1, 'a')]} />))).not.toThrow();
  });

  it('reports only agents that enrolled after the command was issued', () => {
    const known = new Set([1, 2]);
    expect(newAgentsSince([agent(1, 'a'), agent(2, 'b')], known)).toEqual([]);
    expect(newAgentsSince([agent(1, 'a'), agent(2, 'b'), agent(3, 'c')], known).map((a) => a.name)).toEqual(['c']);
    expect(newAgentsSince(undefined, known)).toEqual([]);
  });
});

describe('AddAgentDialog in reinstall mode', () => {
  const target = { id: 7, name: 'web01', platform: 'Windows', last_seen: '2026-03-29T18:51:18Z' };

  it('asks the reinstall route of that agent, valid for an hour by default', () => {
    expect(issueRequest(target, 1)).toEqual({ url: '/api/agents/7/reinstall', body: { ttl_hours: 1 } });
    expect(issueRequest(undefined, 24)).toEqual({ url: '/api/agents/install-tokens', body: { ttl_hours: 24 } });
    expect(issueRequest(undefined, 24, true).body).toEqual({ ttl_hours: 24, probe: true });
    expect(issueRequest(target, 1, true).body).toEqual({ ttl_hours: 1, probe: true });
  });

  it('offers one hour or one day for a reinstall, the longer choices otherwise', () => {
    expect(validityFor(target).map((v) => v.label)).toEqual(['1 hour', '24 hours']);
    expect(validityFor(undefined).map((v) => v.label)).toEqual(['24 hours', '7 days', '30 days']);
  });

  it('starts on the platform the agent runs on', () => {
    expect(initialPlatform(target)).toBe('windows');
    expect(initialPlatform({ ...target, platform: 'Linux' })).toBe('linux');
    expect(initialPlatform({ ...target, platform: null })).toBe('linux');
    expect(initialPlatform(undefined)).toBe('linux');
  });

  it('counts the agent as back only once it reported after the command was issued', () => {
    const issuedAt = Date.parse('2026-11-16T12:00:00Z');
    expect(reconnectedSince({ ...target, last_seen: '2026-11-16T11:59:00Z' }, issuedAt)).toBe(false);
    expect(reconnectedSince({ ...target, last_seen: '2026-11-16T12:00:30Z' }, issuedAt)).toBe(true);
    expect(reconnectedSince({ ...target, last_seen: null }, issuedAt)).toBe(false);
    expect(reconnectedSince(undefined, issuedAt)).toBe(false);
  });

  it('renders in reinstall mode', () => {
    expect(() => renderToStaticMarkup(withQuery(
      <AddAgentDialog open onClose={() => {}} agents={[agent(7, 'web01')]} agent={target} />,
    ))).not.toThrow();
  });
});

describe('canDeployOverSsh', () => {
  const linux = { id: 1, name: 'web01', platform: 'Linux', last_seen: null };
  const windows = { ...linux, platform: 'Windows' };

  it('offers SSH only when the server allows it', () => {
    expect(canDeployOverSsh(true, undefined)).toBe(true);
    expect(canDeployOverSsh(false, undefined)).toBe(false);
    expect(canDeployOverSsh(undefined, undefined)).toBe(false);
  });

  it('offers SSH for Linux agents only', () => {
    expect(canDeployOverSsh(true, linux)).toBe(true);
    expect(canDeployOverSsh(true, windows)).toBe(false);
  });
});
