import { describe, expect, it } from 'vitest';
import {
  DEFAULT_HTTP_FORM,
  hasHttpCheck,
  httpFormFromOptions,
  httpOptionsPayload,
  validateHttpForm,
} from './httpOptions';

describe('validateHttpForm', () => {
  it('accepts the defaults', () => {
    expect(validateHttpForm(DEFAULT_HTTP_FORM)).toBeNull();
  });

  it('accepts status codes and ranges', () => {
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, expected_status: '200-299, 301' })).toBeNull();
  });

  it.each(['abc', '299-200', '99', '600', '200-'])('rejects status "%s"', (status) => {
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, expected_status: status })).not.toBeNull();
  });

  it('rejects keywords with HEAD', () => {
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, method: 'HEAD', keyword: 'ok' })).toMatch(/GET/);
  });

  it('checks the timeout range', () => {
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, timeout: '0' })).not.toBeNull();
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, timeout: '61' })).not.toBeNull();
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, timeout: '30' })).toBeNull();
  });

  it('checks the path / URL', () => {
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, url: '/health' })).toBeNull();
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, url: 'https://example.com/x' })).toBeNull();
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, url: 'health' })).not.toBeNull();
    expect(validateHttpForm({ ...DEFAULT_HTTP_FORM, url: '//evil' })).not.toBeNull();
  });
});

describe('payload round trip', () => {
  it('maps empty strings to null', () => {
    const p = httpOptionsPayload(DEFAULT_HTTP_FORM);
    expect(p.url).toBeNull();
    expect(p.keyword).toBeNull();
    expect(p.timeout).toBe(5);
    expect(p.follow_redirects).toBe(true);
    expect(p.verify_tls).toBe(false);
  });

  it('restores a form from stored options', () => {
    const form = httpFormFromOptions({ method: 'HEAD', url: '/x', timeout: 10, follow_redirects: false });
    expect(form.method).toBe('HEAD');
    expect(form.url).toBe('/x');
    expect(form.timeout).toBe('10');
    expect(form.follow_redirects).toBe(false);
  });
});

describe('hasHttpCheck', () => {
  it('finds http and https tokens', () => {
    expect(hasHttpCheck('icmp,https')).toBe(true);
    expect(hasHttpCheck('http')).toBe(true);
    expect(hasHttpCheck('icmp,tcp:443')).toBe(false);
    expect(hasHttpCheck(null)).toBe(false);
  });
});
