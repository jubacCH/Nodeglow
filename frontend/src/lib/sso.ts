/**
 * Single sign-on (ee): the shapes of /api/identity-providers and the pure
 * helpers of the provider form (no React, unit-tested).
 */

export type ProviderState = 'draft' | 'tested' | 'enabled';
export type DefaultRole = 'readonly' | 'editor' | 'none';
export type RuleRole = 'admin' | 'editor' | 'readonly';
export type LicenceMode = 'full' | 'read_only' | 'off';

export interface RoleRule {
  group: string;
  role: RuleRole;
}

export interface Provider {
  id: number;
  type: 'oidc';
  name: string;
  state: ProviderState;
  enabled: boolean;
  login_key: string;
  sign_in_url: string | null;
  issuer: string;
  client_id: string;
  client_secret_set: boolean;
  scopes: string;
  groups_claim: string;
  ca_pem: string;
  jit_enabled: boolean;
  allowed_domains: string[];
  required_groups: string[];
  role_rules: RoleRule[];
  default_role: DefaultRole;
  issuer_is_own_organisation: boolean;
  session_hours: number;
  sso_required: boolean;
  discovery: Record<string, unknown> | null;
  discovery_at: string | null;
  tested_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  blockers: string[];
  affected_users?: number;
}

export interface ProvidersState {
  providers: Provider[];
  redirect_uri: string | null;
  public_url_set: boolean;
  licence: LicenceMode;
  can_manage: boolean;
  can_delete: boolean;
  sign_in_released: boolean;
  limits: { max_providers: number; session_hours: [number, number] };
  private_issuers: {
    allowed_here: boolean;
    default_tenant: boolean;
    setting: boolean;
    can_change: boolean;
  };
}

export interface CheckResult {
  ok: boolean;
  code?: string;
  message?: string;
  algorithms?: string[];
  signing_keys?: number;
  notes?: string[];
  provider?: Provider;
}

export interface TestStart {
  ok: boolean;
  authorization_url?: string;
  flow?: number;
  code?: string;
  message?: string;
}

export interface TestClaims {
  sub: string | null;
  iss: string | null;
  aud: string[];
  email: string | null;
  email_verified: boolean | null;
  name: string | null;
  preferred_username: string | null;
  groups_claim: string;
  groups: string[];
  groups_present: boolean;
}

export interface TestPreview {
  mail_domain: string | null;
  email_verified: boolean;
  domain_allowed: boolean | null;
  required_groups_met: boolean | null;
  matched_rules: RoleRule[];
  role: RuleRole | null;
  role_from: 'rules' | 'default';
  would_sign_in: boolean;
  new_account: boolean;
  reasons: string[];
}

export interface TestOutcome {
  ok: boolean;
  code: string | null;
  message: string;
  counted?: boolean;
  claims?: TestClaims;
  preview?: TestPreview | null;
  notes?: string[];
  userinfo_used?: boolean;
  provider_error?: string | null;
  provider_error_description?: string | null;
}

export interface TestResultResponse {
  done: boolean;
  provider: { id: number; name: string | null };
  result?: TestOutcome;
}

/** Texts for the errors the callback redirects with (no flow to show). */
export const CALLBACK_ERRORS: Record<string, string> = {
  invalid_request: 'This sign-in cannot be completed here: it was started in another browser, or the link is not valid. Start the test again from the settings.',
  expired: 'This sign-in was already used or took longer than ten minutes. Start the test again.',
  not_released: 'Signing in through single sign-on is not available in this version yet.',
  not_supported: 'This kind of sign-in is not available in this version.',
};

export function callbackErrorText(code: string | null): string {
  return (code && CALLBACK_ERRORS[code]) || CALLBACK_ERRORS.invalid_request;
}

/** One line on what the test decided, for the result page. */
export function verdict(outcome: TestOutcome): { tone: 'ok' | 'warning' | 'down'; text: string } {
  if (!outcome.ok) return { tone: 'down', text: outcome.message };
  const p = outcome.preview;
  if (p && !p.would_sign_in) {
    return { tone: 'warning', text: 'The provider works, but with the current settings this person would not get in.' };
  }
  if (outcome.counted === false) {
    return { tone: 'warning', text: 'The provider works, but its settings changed meanwhile: run the test again.' };
  }
  return { tone: 'ok', text: 'The provider works and every check passed.' };
}

/** What the form edits. Lists are kept as text, one entry per line. */
export interface ProviderForm {
  name: string;
  issuer: string;
  clientId: string;
  clientSecret: string;
  clearSecret: boolean;
  scopes: string;
  groupsClaim: string;
  caPem: string;
  loginKey: string;
  jitEnabled: boolean;
  allowedDomains: string;
  requiredGroups: string;
  roleRules: string;
  defaultRole: DefaultRole;
  ownOrganisation: boolean;
  sessionHours: string;
}

export const EMPTY_FORM: ProviderForm = {
  name: '',
  issuer: '',
  clientId: '',
  clientSecret: '',
  clearSecret: false,
  scopes: 'openid profile email',
  groupsClaim: 'groups',
  caPem: '',
  loginKey: '',
  jitEnabled: true,
  allowedDomains: '',
  requiredGroups: '',
  roleRules: '',
  defaultRole: 'readonly',
  ownOrganisation: false,
  sessionHours: '12',
};

const RULE_ROLES: readonly RuleRole[] = ['admin', 'editor', 'readonly'];

/**
 * Non-empty trimmed lines. With `commas`, commas separate too (mail
 * domains); never for groups, whose names may contain commas (LDAP-style
 * distinguished names).
 */
export function lines(text: string, commas = false): string[] {
  return text.split(commas ? /[\n,]/ : /\n/).map((s) => s.trim()).filter(Boolean);
}

/**
 * Role rules from text, one per line: `<group> = <role>` (or `<group>: <role>`).
 * The last `=`/`:` separates, so group names may contain either.
 */
export function parseRules(text: string): { rules: RoleRule[]; error: string | null } {
  const rules: RoleRule[] = [];
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const at = Math.max(line.lastIndexOf('='), line.lastIndexOf(':'));
    const group = at > 0 ? line.slice(0, at).trim() : '';
    const role = at > 0 ? line.slice(at + 1).trim().toLowerCase() : '';
    if (!group || !RULE_ROLES.includes(role as RuleRole)) {
      return { rules: [], error: `"${line}": write one rule per line as group = admin, editor or readonly.` };
    }
    rules.push({ group, role: role as RuleRole });
  }
  return { rules, error: null };
}

export function rulesText(rules: RoleRule[]): string {
  return rules.map((r) => `${r.group} = ${r.role}`).join('\n');
}

export function formFromProvider(p: Provider): ProviderForm {
  return {
    name: p.name,
    issuer: p.issuer,
    clientId: p.client_id,
    clientSecret: '',
    clearSecret: false,
    scopes: p.scopes,
    groupsClaim: p.groups_claim,
    caPem: p.ca_pem,
    loginKey: p.login_key,
    jitEnabled: p.jit_enabled,
    allowedDomains: p.allowed_domains.join('\n'),
    requiredGroups: p.required_groups.join('\n'),
    roleRules: rulesText(p.role_rules),
    defaultRole: p.default_role,
    ownOrganisation: p.issuer_is_own_organisation,
    sessionHours: String(p.session_hours),
  };
}

/**
 * The JSON body for POST (create) or PATCH (edit). The secret is sent only
 * when typed (blank keeps the stored one); a blank sign-in key is not sent
 * (create: the server picks one; edit: the stored one stays).
 */
export function buildBody(f: ProviderForm): { body: Record<string, unknown>; error: string | null } {
  const { rules, error } = parseRules(f.roleRules);
  if (error) return { body: {}, error };
  const hours = Number(f.sessionHours);
  if (!Number.isInteger(hours)) return { body: {}, error: 'The session lifetime must be a whole number of hours.' };
  const body: Record<string, unknown> = {
    name: f.name.trim(),
    issuer: f.issuer.trim(),
    client_id: f.clientId.trim(),
    scopes: f.scopes.trim(),
    groups_claim: f.groupsClaim.trim(),
    ca_pem: f.caPem.trim(),
    jit_enabled: f.jitEnabled,
    allowed_domains: lines(f.allowedDomains, true),
    required_groups: lines(f.requiredGroups),
    role_rules: rules,
    default_role: f.defaultRole,
    issuer_is_own_organisation: f.ownOrganisation,
    session_hours: hours,
  };
  // Blank: on create the server picks one, on edit the stored one stays.
  if (f.loginKey.trim()) body.login_key = f.loginKey.trim();
  if (f.clearSecret) body.clear_client_secret = true;
  else if (f.clientSecret.trim()) body.client_secret = f.clientSecret;
  return { body, error: null };
}

/** New accounts for everyone the issuer authenticates, without a restriction. */
export function isOpenToEveryone(f: ProviderForm): boolean {
  return f.jitEnabled && lines(f.allowedDomains, true).length === 0 && lines(f.requiredGroups).length === 0;
}

export const STATE_LABEL: Record<ProviderState, string> = {
  draft: 'Draft',
  tested: 'Tested',
  enabled: 'Enabled',
};
