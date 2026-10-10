import { describe, expect, it } from 'vitest';
import { isValidServiceName, parseServiceList, serviceBadge } from './agentServices';

describe('parseServiceList', () => {
  it('splits on newlines and commas, trims and de-duplicates', () => {
    expect(parseServiceList(' nginx \nsshd, nginx\n\n,MSSQL$SQLEXPRESS')).toEqual({
      names: ['nginx', 'sshd', 'MSSQL$SQLEXPRESS'],
      invalid: [],
    });
  });

  it('reports names the backend would reject', () => {
    expect(parseServiceList('nginx\na;b\n--help')).toEqual({ names: ['nginx'], invalid: ['a;b', '--help'] });
  });
});

describe('isValidServiceName', () => {
  it.each(['Spooler', 'getty@tty1.service', 'W32Time'])('accepts %s', (n) => {
    expect(isValidServiceName(n)).toBe(true);
  });
  it.each(['', '-x', 'a|b', 'x'.repeat(257)])('rejects %s', (n) => {
    expect(isValidServiceName(n)).toBe(false);
  });
});

describe('serviceBadge', () => {
  it('maps every state', () => {
    expect(serviceBadge('running').severity).toBe('ok');
    expect(serviceBadge('stopped').severity).toBe('critical');
    expect(serviceBadge('not_found').severity).toBe('warning');
    expect(serviceBadge('unknown').severity).toBe('info');
    expect(serviceBadge(null).label).toBe('No data');
  });
});
