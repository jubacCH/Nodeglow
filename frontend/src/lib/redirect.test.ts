import { describe, expect, it } from 'vitest';
import { loginHref, safeNextPath } from './redirect';

describe('safeNextPath', () => {
  it('keeps same-origin paths with query and hash', () => {
    expect(safeNextPath('/hosts/12')).toBe('/hosts/12');
    expect(safeNextPath('/syslog?severity=3#top')).toBe('/syslog?severity=3#top');
  });

  it.each([
    null,
    '',
    'hosts',
    'https://evil.example/',
    '//evil.example/x',
    '/\\evil.example',
    'javascript:alert(1)',
    '/foo\nbar',
    '/login',
    '/login?next=/x',
  ])('rejects %j', (raw) => {
    expect(safeNextPath(raw)).toBeNull();
  });
});

describe('loginHref', () => {
  it('adds an encoded next param', () => {
    expect(loginHref('/hosts/12?tab=ports')).toBe('/login?next=%2Fhosts%2F12%3Ftab%3Dports');
  });

  it('drops root and unsafe targets', () => {
    expect(loginHref('/')).toBe('/login');
    expect(loginHref('//evil.example')).toBe('/login');
    expect(loginHref('/login')).toBe('/login');
  });
});
