import { describe, expect, it } from 'vitest';
import {
  buildBody, CALLBACK_ERRORS, callbackErrorText, EMPTY_FORM, formFromProvider, isOpenToEveryone, lines,
  parseRules, rulesText, verdict, type Provider,
} from './sso';

const provider: Provider = {
  id: 7, type: 'oidc', name: 'xdigits', state: 'draft', enabled: false, login_key: 'xdigits-k3v9qa',
  sign_in_url: null, issuer: 'https://login.infomaniak.com', client_id: 'cid', client_secret_set: true,
  scopes: 'openid email', groups_claim: 'groups', ca_pem: '', jit_enabled: true,
  allowed_domains: ['xdigits.ch'], required_groups: ['CN=Ops,OU=Groups'],
  role_rules: [{ group: 'admins', role: 'admin' }], default_role: 'none',
  issuer_is_own_organisation: false, session_hours: 8, sso_required: false, discovery: null,
  discovery_at: null, tested_at: null, created_at: null, updated_at: null, blockers: [],
};

describe('lines', () => {
  it('splits lines, and commas only when asked (group names may contain commas)', () => {
    expect(lines(' a \n\n b ')).toEqual(['a', 'b']);
    expect(lines('CN=Ops,OU=Groups')).toEqual(['CN=Ops,OU=Groups']);
    expect(lines('a.ch, b.ch\nc.ch', true)).toEqual(['a.ch', 'b.ch', 'c.ch']);
  });
});

describe('parseRules', () => {
  it('reads one rule per line and round-trips', () => {
    const { rules, error } = parseRules('admins = admin\nCN=Ops,OU=x: Editor\n\n');
    expect(error).toBeNull();
    expect(rules).toEqual([{ group: 'admins', role: 'admin' }, { group: 'CN=Ops,OU=x', role: 'editor' }]);
    expect(parseRules(rulesText(rules)).rules).toEqual(rules);
  });

  it('refuses a line without a known role', () => {
    expect(parseRules('admins = owner').error).toMatch(/admins = owner/);
    expect(parseRules('just a group').error).not.toBeNull();
  });
});

describe('buildBody', () => {
  it('leaves the secret alone unless typed or cleared', () => {
    const f = formFromProvider(provider);
    expect(buildBody(f).body).not.toHaveProperty('client_secret');
    expect(buildBody({ ...f, clientSecret: 'new' }).body.client_secret).toBe('new');
    const cleared = buildBody({ ...f, clientSecret: 'ignored', clearSecret: true }).body;
    expect(cleared.clear_client_secret).toBe(true);
    expect(cleared).not.toHaveProperty('client_secret');
  });

  it('sends the sign-in key only when one is typed', () => {
    expect(buildBody({ ...EMPTY_FORM, name: 'n' }).body).not.toHaveProperty('login_key');
    expect(buildBody(formFromProvider(provider)).body.login_key).toBe('xdigits-k3v9qa');
    // Cleared while editing: the stored key stays (the server refused "" before).
    expect(buildBody({ ...formFromProvider(provider), loginKey: '  ' }).body).not.toHaveProperty('login_key');
  });

  it('sends lists, rules and numbers in the API shape', () => {
    const { body, error } = buildBody({
      ...formFromProvider(provider), allowedDomains: 'a.ch, b.ch', requiredGroups: 'CN=Ops,OU=Groups\nstaff',
    });
    expect(error).toBeNull();
    expect(body).toMatchObject({
      allowed_domains: ['a.ch', 'b.ch'], required_groups: ['CN=Ops,OU=Groups', 'staff'],
      role_rules: [{ group: 'admins', role: 'admin' }], default_role: 'none', session_hours: 8,
    });
    expect(buildBody({ ...EMPTY_FORM, sessionHours: '1.5' }).error).not.toBeNull();
    expect(buildBody({ ...EMPTY_FORM, roleRules: 'x = boss' }).error).not.toBeNull();
  });
});

describe('isOpenToEveryone', () => {
  it('is true only for new accounts without a domain or group restriction', () => {
    expect(isOpenToEveryone(EMPTY_FORM)).toBe(true);
    expect(isOpenToEveryone({ ...EMPTY_FORM, allowedDomains: 'x.ch' })).toBe(false);
    expect(isOpenToEveryone({ ...EMPTY_FORM, requiredGroups: 'staff' })).toBe(false);
    expect(isOpenToEveryone({ ...EMPTY_FORM, jitEnabled: false })).toBe(false);
  });
});

describe('test sign-in results', () => {
  const base = { ok: true, code: null, message: 'ok' };
  const preview = {
    mail_domain: 'beta.ch', email_verified: true, domain_allowed: true, required_groups_met: null,
    matched_rules: [], role: 'readonly' as const, role_from: 'default' as const,
    would_sign_in: true, new_account: true, reasons: [],
  };

  it('names failures, a person who would not get in, and a stale test', () => {
    expect(verdict({ ...base, ok: false, code: 'nonce', message: 'nonce' })).toEqual({ tone: 'down', text: 'nonce' });
    expect(verdict({ ...base, preview: { ...preview, would_sign_in: false } }).tone).toBe('warning');
    expect(verdict({ ...base, preview, counted: false }).tone).toBe('warning');
    expect(verdict({ ...base, preview, counted: true }).tone).toBe('ok');
  });

  it('has a text for every callback error and a fallback', () => {
    for (const code of ['invalid_request', 'expired', 'not_released', 'not_supported']) {
      expect(callbackErrorText(code)).toBe(CALLBACK_ERRORS[code]);
    }
    expect(callbackErrorText('<script>')).toBe(CALLBACK_ERRORS.invalid_request);
    for (const inherited of ['__proto__', 'constructor', 'toString', 'hasOwnProperty']) {
      expect(callbackErrorText(inherited)).toBe(CALLBACK_ERRORS.invalid_request);
    }
    expect(callbackErrorText(null)).toBe(CALLBACK_ERRORS.invalid_request);
  });
});
