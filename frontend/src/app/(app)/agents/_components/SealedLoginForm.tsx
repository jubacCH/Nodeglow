'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Field, Input, Textarea } from '@/components/ui/Field';
import { SegmentedControl } from '@/components/ui/Tabs';
import { apiErrorBody, apiErrorMessage, post } from '@/lib/api';
import { loginFormProblem, parseHosts, type LoginForm, type ProbeCredential } from '@/lib/probeCredentials';
import { sealFingerprint, sealLogin } from '@/lib/seal';
import type { Agent } from '@/types';

const SEAL_KEY_COMMAND = 'sudo /opt/nodeglow/nodeglow-agent seal-key';

/** Plain-English text for the codes the server answers a save with. */
const SAVE_ERRORS: Record<string, string> = {
  sealed_not_enabled: 'This probe does not accept sealed logins. Run enable-deploy on it first.',
  seal_key_missing: 'The probe has not reported a sealing key yet.',
  seal_key_changed: 'The probe reported a different sealing key. Trust it first.',
  wrong_key: 'The probe changed its key while you were typing. Reload and try again.',
  name_taken: 'A login with this name already exists.',
};

const emptyForm = (host: string): LoginForm => ({
  name: '', username: '', auth: 'password', password: '', privateKey: '', passphrase: '', hosts: host,
});

/**
 * Enter a login for deploying through a probe. The browser seals it to the
 * probe's key; the server stores only the ciphertext. Secrets are cleared
 * from state as soon as the save finishes.
 */
export function SealedLoginForm({
  probe, defaultHost, takenNames, onSaved, onCancel,
}: {
  probe: Agent;
  defaultHost: string;
  /** Names already used by the probe's local logins (the saved ones are checked by the caller). */
  takenNames: string[];
  onSaved: (c: ProbeCredential) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<LoginForm>(() => emptyForm(defaultHost.trim()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seal = probe.deploy?.seal;

  const set = (key: keyof LoginForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function pickKeyFile(file: File | undefined) {
    if (!file) return;
    try {
      // Read in the browser only; the file is never uploaded.
      const text = await file.text();
      setForm((f) => ({ ...f, privateKey: text }));
    } catch {
      setError('Could not read that file.');
    }
  }

  async function save() {
    const problem = loginFormProblem(form, takenNames, []);
    if (problem) {
      setError(problem);
      return;
    }
    if (!seal?.public_key || !seal.fingerprint || seal.state !== 'trusted') {
      setError('The probe has no trusted sealing key yet.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Never seal to a key that is not the one shown to (and trusted by) the admin.
      if ((await sealFingerprint(seal.public_key)) !== seal.fingerprint) {
        setError('The probe key does not match its fingerprint. Reload the page and try again.');
        return;
      }
      const name = form.name.trim();
      const username = form.username.trim();
      const hosts = parseHosts(form.hosts);
      const isKey = form.auth === 'key';
      const envelope = await sealLogin(seal.public_key, {
        v: 1,
        id: crypto.randomUUID(),
        name,
        username,
        password: isKey ? null : form.password,
        private_key: isKey ? form.privateKey : null,
        passphrase: isKey && form.passphrase ? form.passphrase : null,
        hosts,
      });
      const res = await post<{ credential: ProbeCredential }>(
        `/api/probes/${probe.id}/credentials`,
        { name, username, auth: form.auth, hosts, sealed: envelope });
      setForm(emptyForm(defaultHost.trim()));
      onSaved(res.credential);
    } catch (e) {
      const code = apiErrorBody(e)?.code;
      setError((code && SAVE_ERRORS[code]) || apiErrorMessage(e, 'Could not save the login'));
    } finally {
      // Whatever happened, the secrets do not stay in state longer than needed.
      setForm((f) => ({ ...f, password: '', privateKey: '', passphrase: '' }));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name" required hint="Letters, digits and _ . @ -">
          <Input value={form.name} onChange={set('name')} placeholder="lab-root" autoComplete="off" />
        </Field>
        <Field label="Username" required>
          <Input value={form.username} onChange={set('username')} placeholder="root" autoComplete="off" />
        </Field>
      </div>
      <SegmentedControl
        label="Sign in with"
        value={form.auth}
        onChange={(auth) => setForm((f) => ({ ...f, auth, password: '', privateKey: '', passphrase: '' }))}
        options={[
          { value: 'password', label: 'Password' },
          { value: 'key', label: 'Private key' },
        ]}
      />
      {form.auth === 'password' ? (
        <Field label="Password" required>
          <Input type="password" value={form.password} onChange={set('password')} autoComplete="new-password" />
        </Field>
      ) : (
        <>
          <Field label="Private key" required hint="Paste it or pick the file. It is read in your browser and sealed before it is sent.">
            <Textarea
              value={form.privateKey}
              onChange={set('privateKey')}
              rows={5}
              spellCheck={false}
              autoComplete="off"
              className="font-code"
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
            />
          </Field>
          <Field label="Key file">
            <Input
              type="file"
              onChange={(e) => {
                void pickKeyFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
          </Field>
          <Field label="Passphrase" hint="Only if the key is encrypted.">
            <Input type="password" value={form.passphrase} onChange={set('passphrase')} autoComplete="new-password" />
          </Field>
        </>
      )}
      <Field label="Hosts" required hint="Where the probe may use this login: host names, IPs or CIDR ranges, separated by commas.">
        <Input value={form.hosts} onChange={set('hosts')} placeholder="10.0.0.5, 10.0.1.0/24" autoComplete="off" />
      </Field>
      <div className="rounded-ctl border border-border bg-surface-2 px-3 py-2 text-meta text-fg-2">
        <p>
          Sealed to the probe key{' '}
          <span className="break-all font-code text-fg">{seal?.fingerprint ?? 'unknown'}</span>.
        </p>
        <p className="mt-1">
          Compare it with <span className="font-code">{SEAL_KEY_COMMAND}</span> on the probe.
        </p>
      </div>
      {error && <p role="alert" className="text-meta text-down">{error}</p>}
      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Sealing…' : 'Seal and save'}</Button>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </div>
  );
}
