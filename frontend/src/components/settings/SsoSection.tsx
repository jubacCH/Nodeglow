'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, KeyRound, Plug, Plus, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { CopyButton } from '@/components/ui/CopyButton';
import { Checkbox, Field, Input, Select, Switch, Textarea } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { StatusPill } from '@/components/ui/StatusPill';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorBody, apiErrorMessage, del, get, patch, post, put } from '@/lib/api';
import {
  buildBody, EMPTY_FORM, formFromProvider, isOpenToEveryone, STATE_LABEL,
  type CheckResult, type Provider, type ProviderForm, type ProvidersState,
} from '@/lib/sso';
import type { HealthState } from '@/lib/status';
import { useToastStore } from '@/stores/toast';
import { Notice, SettingsSection } from './formKit';

export const SSO_KEY = ['identity-providers'] as const;

const STATE_TONE: Record<Provider['state'], HealthState> = {
  draft: 'unknown',
  tested: 'ok',
  enabled: 'ok',
};

/**
 * Settings → Authentication → Single sign-on (ee). A tenant admin prepares
 * the tenant's OpenID Connect providers here: issuer, client, who may get an
 * account, roles from groups. Signing in through them comes with a later
 * version; until then a provider stays a draft that can be checked.
 */
export function SsoSection() {
  const query = useQuery<ProvidersState>({
    queryKey: SSO_KEY,
    queryFn: () => get<ProvidersState>('/api/identity-providers'),
  });
  return (
    <QueryState query={query} errorTitle="Could not load the identity providers">
      {(state) => <Providers state={state} />}
    </QueryState>
  );
}

function Providers({ state }: { state: ProvidersState }) {
  const qc = useQueryClient();
  const toast = useToastStore();
  const { confirm, ConfirmDialogElement } = useConfirm();
  // null: list only; 'new': the create form; a number: editing that provider.
  const [editing, setEditing] = useState<null | 'new' | number>(null);
  const [checks, setChecks] = useState<Record<number, CheckResult>>({});
  const [checking, setChecking] = useState<number | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: SSO_KEY });
  const current = typeof editing === 'number' ? state.providers.find((p) => p.id === editing) : undefined;

  async function check(p: Provider) {
    setChecking(p.id);
    try {
      const result = await post<CheckResult>(`/api/identity-providers/${p.id}/check`);
      setChecks((c) => ({ ...c, [p.id]: result }));
      await refresh();
    } catch (e) {
      setChecks((c) => ({ ...c, [p.id]: { ok: false, message: apiErrorMessage(e, 'The check could not run') } }));
    } finally {
      setChecking(null);
    }
  }

  async function remove(p: Provider) {
    const detail = await get<Provider>(`/api/identity-providers/${p.id}`).catch(() => p);
    const people = detail.affected_users ?? 0;
    const ok = await confirm({
      title: `Delete ${p.name}?`,
      description: people
        ? `${people} linked account(s) lose this sign-in; accounts that exist only through it are removed from this tenant. Their sessions end at once.`
        : 'Nobody is linked to this provider yet.',
      confirmLabel: 'Delete provider',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await del(`/api/identity-providers/${p.id}`);
      if (editing === p.id) setEditing(null);
      await refresh();
      toast.show('Identity provider deleted', 'success');
    } catch (e) {
      toast.show(apiErrorMessage(e, 'The provider could not be deleted'), 'error');
    }
  }

  const canAdd = state.can_manage && state.providers.length < state.limits.max_providers;

  return (
    <SettingsSection
      id="sso"
      title="Single sign-on (OpenID Connect)"
      description="Let the people of this tenant sign in with their company account: Entra ID, Google, authentik, Keycloak, Infomaniak or any other OpenID Connect provider."
      actions={canAdd && editing === null ? (
        <Button size="sm" onClick={() => setEditing('new')}>
          <Plus size={13} aria-hidden="true" /> Add provider
        </Button>
      ) : undefined}
    >
      {ConfirmDialogElement}
      {!state.sign_in_released && (
        <Notice tone="info" className="mb-4">
          Signing in through single sign-on comes with a later version. You can prepare and check providers now;
          nobody can sign in through them yet, and local accounts keep working as they do.
        </Notice>
      )}
      {state.licence === 'off' && (
        <Notice tone="warning" className="mb-4">Single sign-on needs a Nodeglow Enterprise license with this feature.</Notice>
      )}
      {state.licence === 'read_only' && (
        <Notice tone="warning" className="mb-4">
          The Nodeglow Enterprise license has expired: providers can be looked at and deleted, not changed.
        </Notice>
      )}

      <RedirectInfo state={state} />

      {state.providers.length === 0 && editing === null && (
        <p className="text-ui text-fg-2">No identity provider is set up for this tenant.</p>
      )}

      <ul className="space-y-3">
        {state.providers.map((p) => (
          <li key={p.id} className="rounded-ctl border border-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <KeyRound size={14} aria-hidden="true" className="text-fg-3" />
                  <span className="truncate font-medium text-fg">{p.name}</span>
                  <StatusPill status={STATE_TONE[p.state]} size="sm">{STATE_LABEL[p.state]}</StatusPill>
                </div>
                <p className="mt-0.5 truncate font-mono text-meta text-fg-3">{p.issuer}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {state.can_manage && (
                  <>
                    <Button variant="secondary" size="sm" loading={checking === p.id}
                            disabled={checking !== null} onClick={() => void check(p)}>
                      {checking !== p.id && <Plug size={13} aria-hidden="true" />} Check connection
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => setEditing(p.id)}>Edit</Button>
                  </>
                )}
                {state.can_delete && (
                  <Button variant="ghost" size="sm" onClick={() => void remove(p)}>Delete</Button>
                )}
              </div>
            </div>
            {p.sign_in_url && (
              <p className="mt-2 flex items-center gap-1.5 text-meta text-fg-2">
                Sign-in address: <code className="font-mono">{p.sign_in_url}</code>
                <CopyButton text={p.sign_in_url} />
              </p>
            )}
            {checks[p.id] && <CheckNotice result={checks[p.id]} />}
            {p.blockers.length > 0 && (
              <details className="mt-2 text-meta text-fg-2">
                <summary className="cursor-pointer">What is missing before it can be enabled ({p.blockers.length})</summary>
                <ul className="ml-4 mt-1 list-disc space-y-0.5">
                  {p.blockers.map((b) => <li key={b}>{b}</li>)}
                </ul>
              </details>
            )}
            {editing === p.id && current && (
              <ProviderEditor key={p.id} provider={current} state={state} onDone={() => { setEditing(null); void refresh(); }} />
            )}
          </li>
        ))}
      </ul>

      {editing === 'new' && (
        <div className="mt-3 rounded-ctl border border-border p-3">
          <ProviderEditor state={state} onDone={() => { setEditing(null); void refresh(); }} />
        </div>
      )}

      {state.private_issuers.can_change && <PrivateIssuers state={state} onChanged={refresh} />}
    </SettingsSection>
  );
}

function RedirectInfo({ state }: { state: ProvidersState }) {
  if (!state.redirect_uri) {
    return (
      <Notice tone="warning" className="mb-4">
        Set the public URL of this installation first (Settings → Notifications → Public URL). The redirect address
        you register at the provider is built from it.
      </Notice>
    );
  }
  return (
    <div className="mb-4 text-ui text-fg-2">
      Redirect address to register at the provider:
      <span className="ml-1.5 inline-flex items-center gap-1.5">
        <code className="font-mono text-meta text-fg">{state.redirect_uri}</code>
        <CopyButton text={state.redirect_uri} />
      </span>
    </div>
  );
}

function CheckNotice({ result }: { result: CheckResult }) {
  return (
    <Notice tone={result.ok ? 'ok' : 'down'} className="mt-2">
      <span className="flex items-start gap-2">
        {result.ok ? <CheckCircle2 size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          : <XCircle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />}
        <span>
          {result.ok
            ? `Connection works: ${result.signing_keys} signing key(s), id tokens signed with ${(result.algorithms ?? []).join(', ')}.`
            : result.message}
          {result.ok && result.notes && result.notes.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-fg-2">
              {result.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          )}
        </span>
      </span>
    </Notice>
  );
}

function PrivateIssuers({ state, onChanged }: { state: ProvidersState; onChanged: () => void }) {
  const toast = useToastStore();
  const [busy, setBusy] = useState(false);
  async function change(value: boolean) {
    setBusy(true);
    try {
      await put('/api/identity-providers/installation', { private_issuers: value });
      onChanged();
    } catch (e) {
      toast.show(apiErrorMessage(e, 'The setting could not be saved'), 'error');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-5 border-t border-border pt-4">
      <Switch
        checked={state.private_issuers.setting}
        disabled={busy || !state.can_manage}
        onChange={(v) => void change(v)}
        label="Identity providers of every tenant may use private addresses"
        description="Installation setting. Off: only the providers of the default tenant may sit in a private network; every other tenant's provider must be reachable on a public address. Turn it on only when the customers' identity providers are in networks connected to this installation."
      />
    </div>
  );
}

function ProviderEditor({ provider, state, onDone }: { provider?: Provider; state: ProvidersState; onDone: () => void }) {
  const toast = useToastStore();
  const creating = !provider;
  const [f, setF] = useState<ProviderForm>(provider ? formFromProvider(provider) : EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [field, setField] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof ProviderForm>(k: K, v: ProviderForm[K]) => setF((cur) => ({ ...cur, [k]: v }));
  const errorFor = (name: string) => (field === name ? error : undefined);

  async function save() {
    const { body, error: formError } = buildBody(f);
    if (formError) {
      setError(formError);
      setField('role_rules');
      return;
    }
    setBusy(true);
    setError(null);
    setField(null);
    try {
      if (creating) await post('/api/identity-providers', body);
      else await patch(`/api/identity-providers/${provider.id}`, body);
      toast.show(creating ? 'Identity provider added' : 'Identity provider saved', 'success');
      onDone();
    } catch (e) {
      const b = apiErrorBody(e);
      setError(apiErrorMessage(e, 'The provider could not be saved'));
      setField(typeof b?.field === 'string' ? b.field : null);
    } finally {
      setBusy(false);
    }
  }

  const [minHours, maxHours] = state.limits.session_hours;
  return (
    <form
      className="mt-3 space-y-4"
      onSubmit={(e) => { e.preventDefault(); void save(); }}
      noValidate
      aria-label={creating ? 'New identity provider' : `Edit ${provider.name}`}
    >
      {error && !field && <Notice tone="down">{error}</Notice>}
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name" required hint="Shown on the sign-in button." error={errorFor('name')}>
          <Input value={f.name} maxLength={128} onChange={(e) => set('name', e.target.value)} placeholder="xdigits (Infomaniak)" />
        </Field>
        <Field label="Issuer" required hint="Exactly as the provider states it, e.g. https://login.infomaniak.com" error={errorFor('issuer')}>
          <Input className="font-mono" inputMode="url" value={f.issuer} onChange={(e) => set('issuer', e.target.value)} placeholder="https://" />
        </Field>
        <Field label="Client ID" required error={errorFor('client_id')}>
          <Input className="font-mono" value={f.clientId} onChange={(e) => set('clientId', e.target.value)} autoComplete="off" />
        </Field>
        <Field
          label={<>Client secret{provider?.client_secret_set && <span className="ml-1.5 font-normal text-ok">· stored</span>}</>}
          hint={provider?.client_secret_set ? 'Leave blank to keep the stored secret. It is never shown again.' : 'Stored encrypted with this tenant’s key.'}
          error={errorFor('client_secret')}
        >
          <Input type="password" autoComplete="new-password" value={f.clientSecret} disabled={f.clearSecret}
                 onChange={(e) => set('clientSecret', e.target.value)} placeholder={provider?.client_secret_set ? '••••••••' : ''} />
        </Field>
      </div>
      {provider?.client_secret_set && (
        <Checkbox label="Remove the stored client secret" checked={f.clearSecret}
                  onChange={(e) => set('clearSecret', e.target.checked)} />
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Scopes" hint="Must include openid." error={errorFor('scopes')}>
          <Input className="font-mono" value={f.scopes} onChange={(e) => set('scopes', e.target.value)} />
        </Field>
        <Field label="Groups claim" hint="The claim that lists the person’s groups." error={errorFor('groups_claim')}>
          <Input className="font-mono" value={f.groupsClaim} onChange={(e) => set('groupsClaim', e.target.value)} />
        </Field>
        <Field label="Sign-in key" hint={creating ? 'Leave blank to get one. Part of the sign-in address.' : 'Part of the sign-in address. Short keys are easy to guess.'}
               error={errorFor('login_key')}>
          <Input className="font-mono" value={f.loginKey} onChange={(e) => set('loginKey', e.target.value.toLowerCase())} placeholder="acme-k3v9qa" />
        </Field>
      </div>

      <fieldset className="space-y-3 border-t border-border pt-4">
        <legend className="text-ui font-medium text-fg">Who may sign in</legend>
        <Switch checked={f.jitEnabled} onChange={(v) => set('jitEnabled', v)}
                label="Create an account at the first sign-in"
                description="Off: only people an admin has invited can sign in." />
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Allowed mail domains" hint="One per line. Only addresses the provider marks as verified count." error={errorFor('allowed_domains')}>
            <Textarea className="font-mono" rows={3} value={f.allowedDomains} onChange={(e) => set('allowedDomains', e.target.value)} placeholder="xdigits.ch" />
          </Field>
          <Field label="Required groups" hint="One per line. A person needs at least one of them." error={errorFor('required_groups')}>
            <Textarea className="font-mono" rows={3} value={f.requiredGroups} onChange={(e) => set('requiredGroups', e.target.value)} />
          </Field>
        </div>
        {isOpenToEveryone(f) && (
          <Notice tone="warning">
            <Checkbox
              label="This issuer only signs in people of my organisation"
              checked={f.ownOrganisation}
              onChange={(e) => set('ownOrganisation', e.target.checked)}
            />
            <p className="mt-1">
              Without a mail domain or a group, everyone the issuer signs in would get an account. That is fine for an
              Entra tenant or an authentik or Keycloak realm of your own company. It is not for Infomaniak, Google or any
              issuer where anybody can have an account: restrict it above.
            </p>
          </Notice>
        )}
      </fieldset>

      <fieldset className="space-y-3 border-t border-border pt-4">
        <legend className="text-ui font-medium text-fg">Roles</legend>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Roles from groups" hint="One rule per line: group = admin, editor or readonly. With rules, the provider decides the role at every sign-in."
                 error={errorFor('role_rules')}>
            <Textarea className="font-mono" rows={3} value={f.roleRules} onChange={(e) => set('roleRules', e.target.value)} placeholder="nodeglow-admins = admin" />
          </Field>
          <div className="space-y-4">
            <Field label="Role without a matching rule" error={errorFor('default_role')}>
              <Select value={f.defaultRole} onChange={(e) => set('defaultRole', e.target.value as ProviderForm['defaultRole'])}>
                <option value="readonly">readonly</option>
                <option value="editor">editor</option>
                <option value="none">no access</option>
              </Select>
            </Field>
            <Field label="Session lifetime (hours)" hint={`${minHours} to ${maxHours}. Ends the access of someone removed at the provider.`}
                   error={errorFor('session_hours')}>
              <Input type="number" min={minHours} max={maxHours} value={f.sessionHours} onChange={(e) => set('sessionHours', e.target.value)} />
            </Field>
          </div>
        </div>
      </fieldset>

      <Field label="CA certificate (optional)" hint="Only for a provider whose TLS certificate comes from a private CA. PEM." error={errorFor('ca_pem')}>
        <Textarea className="font-mono text-meta" rows={3} value={f.caPem} onChange={(e) => set('caPem', e.target.value)}
                  placeholder="-----BEGIN CERTIFICATE-----" />
      </Field>

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone} disabled={busy}>Cancel</Button>
        <Button type="submit" loading={busy} disabled={busy}>{creating ? 'Add provider' : 'Save'}</Button>
      </div>
    </form>
  );
}
