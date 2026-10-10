'use client';

import { useState, type ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Plug, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Checkbox, Field, Input, Switch } from '@/components/ui/Field';
import { api, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { Notice, SaveBar, SettingsSection, useSaveStatus, type SectionForm } from './formKit';
import { buildLdapFormData, type LdapForm, type SettingsData } from './settingsForm';

type TestResult = { ok: boolean; error?: string; users_found?: number };

function Group({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <fieldset className="border-t border-border pt-4">
      <legend className="sr-only">{title}</legend>
      <h3 aria-hidden="true" className="mb-1 text-ui font-medium text-fg">{title}</h3>
      {description && <p className="mb-3 text-meta text-fg-2">{description}</p>}
      {!description && <div className="mb-3" />}
      {children}
    </fieldset>
  );
}

export function AuthTab({
  settings, ldap, onSaved,
}: {
  settings: SettingsData;
  ldap: SectionForm<LdapForm> & { value: LdapForm };
  /** Invalidate the settings query after a save. */
  onSaved: () => void;
}) {
  const toast = useToastStore();
  const f = ldap.value;
  const save = useSaveStatus(ldap.dirty);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  async function handleSave() {
    save.start();
    try {
      const saved = await api<{ ok: boolean; warnings?: string[] }>('/settings/ldap/save', { method: 'POST', body: buildLdapFormData(f) });
      const w = saved?.warnings ?? [];
      setWarnings(w);
      toast.show(w.length ? 'LDAP settings saved — connection is unencrypted' : 'LDAP settings saved', w.length ? 'warning' : 'success');
      ldap.markSaved();
      save.succeed();
      onSaved();
    } catch {
      save.fail('the server rejected the request');
      toast.show('Failed to save LDAP settings', 'error');
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      // Save first (with LDAP on), then test — as before.
      const saved = await api<{ ok: boolean; warnings?: string[] }>('/settings/ldap/save', { method: 'POST', body: buildLdapFormData(f, true) });
      setWarnings(saved?.warnings ?? []);
      const res = await post<TestResult>('/settings/ldap/test', {});
      setTestResult(res);
    } catch {
      setTestResult({ ok: false, error: 'Request failed' });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-4">
      <SettingsSection
        id="ldap"
        title="LDAP / Active Directory"
        description="Authenticate users against your directory. Local accounts keep working as a fallback."
      >
        <Switch
          checked={f.ldapEnabled}
          onChange={(v) => ldap.set('ldapEnabled', v)}
          label="Enable LDAP authentication"
          className="mb-4"
        />

        {f.ldapEnabled && (
          <div className="space-y-4">
            <Group title="Connection">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Server URL" hint="ldap:// or ldaps:// with optional port">
                  <Input className="font-mono" placeholder="ldap://dc01.example.com:389" value={f.ldapServer} onChange={(e) => ldap.set('ldapServer', e.target.value)} />
                </Field>
                <Field label="Base DN">
                  <Input className="font-mono" placeholder="dc=example,dc=com" value={f.ldapBaseDn} onChange={(e) => ldap.set('ldapBaseDn', e.target.value)} />
                </Field>
                <Field label="Bind DN" hint="Service account for user lookups">
                  <Input className="font-mono" placeholder="cn=admin,dc=example,dc=com" value={f.ldapBindDn} onChange={(e) => ldap.set('ldapBindDn', e.target.value)} />
                </Field>
                <Field
                  label={<>Bind password{settings.ldap_has_bind_pw && <span className="ml-1.5 font-normal text-ok">· stored</span>}</>}
                  hint={settings.ldap_has_bind_pw ? 'Leave blank to keep the stored password.' : undefined}
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    placeholder={settings.ldap_has_bind_pw ? '••••••••' : ''}
                    value={f.ldapBindPassword}
                    onChange={(e) => ldap.set('ldapBindPassword', e.target.value)}
                  />
                </Field>
              </div>
              <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3">
                <Checkbox label="SSL (ldaps://)" checked={f.ldapUseSsl} onChange={(e) => ldap.set('ldapUseSsl', e.target.checked)} />
                <Checkbox label="StartTLS" checked={f.ldapStartTls} onChange={(e) => ldap.set('ldapStartTls', e.target.checked)} />
                <Checkbox label="Verify TLS certificate" checked={f.ldapTlsVerify} onChange={(e) => ldap.set('ldapTlsVerify', e.target.checked)} />
              </div>
              {!f.ldapTlsVerify && (f.ldapUseSsl || f.ldapStartTls) && (
                <Notice tone="warning" className="mt-3">
                  Certificate verification is off — the connection is encrypted but the server&apos;s identity is not checked.
                </Notice>
              )}
            </Group>

            <Group title="User search">
              <div className="grid gap-4 md:grid-cols-2">
                <Field
                  className="md:col-span-2"
                  label="User filter"
                  hint={'Use {username} as placeholder. AD: (&(objectClass=person)(sAMAccountName={username})) · OpenLDAP: (&(objectClass=inetOrgPerson)(uid={username}))'}
                >
                  <Input className="font-mono text-meta" value={f.ldapUserFilter} onChange={(e) => ldap.set('ldapUserFilter', e.target.value)} />
                </Field>
                <Field label="Display name attribute">
                  <Input className="font-mono" value={f.ldapDisplayAttr} onChange={(e) => ldap.set('ldapDisplayAttr', e.target.value)} />
                </Field>
              </div>
            </Group>

            <Group title="Role mapping (optional)" description="Map LDAP groups to Nodeglow roles. Users without a matching group get “readonly”.">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Group attribute">
                  <Input className="font-mono" value={f.ldapGroupAttr} onChange={(e) => ldap.set('ldapGroupAttr', e.target.value)} />
                </Field>
                <div className="max-md:hidden" />
                <Field label="Admin group (CN)">
                  <Input className="font-mono" placeholder="CN=Nodeglow-Admins,OU=Groups,DC=..." value={f.ldapAdminGroup} onChange={(e) => ldap.set('ldapAdminGroup', e.target.value)} />
                </Field>
                <Field label="Editor group (CN)">
                  <Input className="font-mono" placeholder="CN=Nodeglow-Editors,OU=Groups,DC=..." value={f.ldapEditorGroup} onChange={(e) => ldap.set('ldapEditorGroup', e.target.value)} />
                </Field>
              </div>
            </Group>

            <Group title="Connection test" description="Saves the settings above, then binds and searches with the filter.">
              <Button variant="secondary" size="sm" loading={testing} disabled={testing || !f.ldapServer} onClick={handleTest}>
                {!testing && <Plug size={13} aria-hidden="true" />}
                {testing ? 'Testing…' : 'Test connection'}
              </Button>
              {testResult && (
                <Notice tone={testResult.ok ? 'ok' : 'down'} className="mt-3">
                  <span className="flex items-center gap-2">
                    {testResult.ok ? <CheckCircle2 size={16} aria-hidden="true" /> : <XCircle size={16} aria-hidden="true" />}
                    {testResult.ok
                      ? `Connection successful — ${testResult.users_found} user(s) found matching filter`
                      : testResult.error}
                  </span>
                </Notice>
              )}
            </Group>
          </div>
        )}

        {/* Transport warnings from the last save */}
        {warnings.length > 0 && (
          <Notice tone="warning" className="mt-4 space-y-1">
            {warnings.map((w) => (
              <span key={w} className="flex items-start gap-2">
                <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
                {w}
              </span>
            ))}
          </Notice>
        )}

        <SaveBar status={save.status} onSave={handleSave} onDiscard={ldap.discard} label="Save LDAP settings" sticky={false} />
      </SettingsSection>

      <SettingsSection id="ldap-how" title="How it works">
        <ul className="list-inside list-disc space-y-1.5 text-ui text-fg-2">
          <li>When LDAP is enabled, users are authenticated against your LDAP/AD server first</li>
          <li>Local accounts (including the initial admin) still work as fallback</li>
          <li>LDAP users are auto-created on first login — no manual provisioning needed</li>
          <li>Roles are mapped from LDAP groups on each login (admin → editor → readonly)</li>
          <li>If no group mapping is configured, LDAP users get the &quot;readonly&quot; role</li>
        </ul>
      </SettingsSection>
    </div>
  );
}
