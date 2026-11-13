'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, ShieldOff } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { CopyButton } from '@/components/ui/CopyButton';
import { Field, Input } from '@/components/ui/Field';
import { QrCode } from '@/components/ui/QrCode';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { api, apiErrorMessage, get, post } from '@/lib/api';
import { formatSecret, type MfaStatus } from '@/lib/mfa';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

/* ─── Password ─── */

function PasswordCard() {
  const toast = useToastStore((s) => s.show);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== repeat) {
      setError('The two new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await api('/users/me/password', {
        method: 'POST',
        body: new URLSearchParams({ current_password: current, password: next }),
      });
      setCurrent(''); setNext(''); setRepeat('');
      toast('Password changed. Other sessions of this account were signed out.', 'success');
    } catch (err) {
      setError(apiErrorMessage(err, 'The password could not be changed.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card as="section" aria-labelledby="account-password">
      <CardHeader title="Password" titleId="account-password" />
      <form onSubmit={submit} className="max-w-md space-y-4">
        <Field label="Current password">
          <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        </Field>
        <Field label="New password" hint="At least 8 characters.">
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={8} required />
        </Field>
        <Field label="Repeat new password">
          <Input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" minLength={8} required />
        </Field>
        {error && <p role="alert" className="text-meta text-down">{error}</p>}
        <Button type="submit" loading={busy}>Change password</Button>
      </form>
    </Card>
  );
}

/* ─── Two-factor sign-in ─── */

interface SetupData { secret: string; otpauth_uri: string }

function TwoFactorCard() {
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  const query = useQuery({ queryKey: ['mfa'], queryFn: () => get<MfaStatus>('/api/account/mfa') });
  const [setup, setSetup] = useState<SetupData | null>(null);
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [code, setCode] = useState('');
  const [turningOff, setTurningOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(apiErrorMessage(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  const start = () => run(async () => {
    setSetup(await post<SetupData>('/api/account/mfa/setup'));
    setCode('');
  }, 'The setup could not be started.');

  const confirm = (e: FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const res = await post<{ recovery_codes: string[] }>('/api/account/mfa/enable', { code: code.trim() });
      setRecovery(res.recovery_codes);
      setSetup(null);
      setCode('');
      await qc.invalidateQueries({ queryKey: ['mfa'] });
    }, 'That code is not valid.');
  };

  const turnOff = (e: FormEvent) => {
    e.preventDefault();
    return run(async () => {
      await post('/api/account/mfa/disable', { code: code.trim() });
      setTurningOff(false);
      setCode('');
      setRecovery(null);
      await qc.invalidateQueries({ queryKey: ['mfa'] });
      toast('Two-factor sign-in is off.', 'success');
    }, 'That code is not valid.');
  };

  return (
    <Card as="section" aria-labelledby="account-mfa">
      <CardHeader title="Two-factor sign-in" titleId="account-mfa" />
      <QueryState
        query={query}
        isEmpty={() => false}
        errorTitle="Could not load the two-factor status"
        loading={<Skeleton className="h-16 w-full" />}
      >
        {(status) => (
          <div className="max-w-xl space-y-4">
            <p className="flex items-start gap-2 text-ui text-fg-2">
              {status.enabled
                ? <ShieldCheck size={16} className="mt-0.5 shrink-0 text-ok" aria-hidden="true" />
                : <ShieldOff size={16} className="mt-0.5 shrink-0 text-fg-3" aria-hidden="true" />}
              <span>
                {status.enabled
                  ? `On. Signing in asks for a code from your authenticator app. ${status.recovery_codes_left} recovery code${status.recovery_codes_left === 1 ? '' : 's'} left.`
                  : 'Off. With it on, signing in needs your password and a code from an authenticator app on your phone, so a stolen password alone opens nothing.'}
              </span>
            </p>

            {recovery && (
              <div className="rounded-ctl border border-border bg-surface-2 p-4">
                <p className="mb-2 text-ui font-medium text-fg">Recovery codes</p>
                <p className="mb-3 text-meta text-fg-2">
                  Each code signs you in once if you lose your phone. They are shown only now: store them somewhere safe.
                </p>
                <ul className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-ui text-fg">
                  {recovery.map((c) => <li key={c}>{c}</li>)}
                </ul>
                <div className="mt-3 flex items-center gap-2">
                  <CopyButton text={recovery.join('\n')} />
                  <span className="text-meta text-fg-3">Copy all</span>
                  <Button variant="secondary" size="sm" className="ml-auto" onClick={() => setRecovery(null)}>I have stored them</Button>
                </div>
              </div>
            )}

            {!status.enabled && !setup && (
              <Button onClick={start} loading={busy}>Set up two-factor sign-in</Button>
            )}

            {!status.enabled && setup && (
              <form onSubmit={confirm} className="space-y-4">
                <ol className="list-decimal space-y-3 pl-5 text-ui text-fg-2">
                  <li>
                    Open an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Aegis, 1Password, …) and scan this code:
                    <span className="mt-2 block">
                      <QrCode value={setup.otpauth_uri} label="QR code for the authenticator app" />
                    </span>
                    <span className="mt-2 block text-meta text-fg-3">Cannot scan? Enter this setup key in the app instead:</span>
                    <span className="mt-1 flex items-center gap-2">
                      <code className="rounded-chip bg-surface-2 px-2 py-1 font-mono text-ui text-fg">{formatSecret(setup.secret)}</code>
                      <CopyButton text={setup.secret} />
                    </span>
                    <a href={setup.otpauth_uri} className="mt-1.5 inline-block text-meta font-medium text-accent hover:text-accent-hover">
                      On this device: open in the authenticator app
                    </a>
                  </li>
                  <li>Enter the six-digit code the app shows.</li>
                </ol>
                <Field label="Code from the app">
                  <Input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" inputMode="numeric" className="max-w-[160px]" required />
                </Field>
                {error && <p role="alert" className="text-meta text-down">{error}</p>}
                <div className="flex gap-2">
                  <Button type="submit" loading={busy}>Turn on</Button>
                  <Button type="button" variant="secondary" onClick={() => { setSetup(null); setError(null); }}>Cancel</Button>
                </div>
              </form>
            )}

            {status.enabled && !turningOff && (
              <Button variant="secondary" onClick={() => { setTurningOff(true); setCode(''); setError(null); }}>Turn off…</Button>
            )}

            {status.enabled && turningOff && (
              <form onSubmit={turnOff} className="space-y-4">
                <Field label="Code from the app, or a recovery code" hint="Needed to turn two-factor sign-in off.">
                  <Input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" className="max-w-[220px]" required />
                </Field>
                {error && <p role="alert" className="text-meta text-down">{error}</p>}
                <div className="flex gap-2">
                  <Button type="submit" variant="danger" loading={busy}>Turn off</Button>
                  <Button type="button" variant="secondary" onClick={() => { setTurningOff(false); setError(null); }}>Cancel</Button>
                </div>
              </form>
            )}

            {!status.enabled && !setup && error && <p role="alert" className="text-meta text-down">{error}</p>}
          </div>
        )}
      </QueryState>
    </Card>
  );
}

/* ─── Page ─── */

export default function AccountPage() {
  useEffect(() => { document.title = 'Account | Nodeglow'; }, []);
  const user = useAuthStore((s) => s.user);
  return (
    <div>
      <PageHeader title="Account" description={user ? `Signed in as ${user.username}` : undefined} />
      <div className="space-y-4">
        <PasswordCard />
        <TwoFactorCard />
      </div>
    </div>
  );
}
