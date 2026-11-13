import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Agent } from '@/types';
import { AddAgentDialog, newAgentsSince } from './AddAgentDialog';

const agent = (id: number, name: string) => ({ id, name, hostname: name }) as Agent;

describe('AddAgentDialog', () => {
  it('renders before any token exists', () => {
    // The dialog used to read an enrollment key the server no longer sends
    // and crashed the whole page on open.
    expect(() => renderToStaticMarkup(<AddAgentDialog open onClose={() => {}} agents={undefined} />)).not.toThrow();
    expect(() => renderToStaticMarkup(<AddAgentDialog open onClose={() => {}} agents={[agent(1, 'a')]} />)).not.toThrow();
  });

  it('reports only agents that enrolled after the command was issued', () => {
    const known = new Set([1, 2]);
    expect(newAgentsSince([agent(1, 'a'), agent(2, 'b')], known)).toEqual([]);
    expect(newAgentsSince([agent(1, 'a'), agent(2, 'b'), agent(3, 'c')], known).map((a) => a.name)).toEqual(['c']);
    expect(newAgentsSince(undefined, known)).toEqual([]);
  });
});
